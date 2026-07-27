"""Improve-overview service.

Splits an overweight primary-instruction "overview" of a MANUAL FILE AGENT into
THREE buckets, chosen so each piece of content lands where retrieval can
actually reach it:

  - dictionary_text  → the lean always-loaded primary instruction (column & term
    meanings, joins, quirks). Force-loaded on every query.
  - metric_instructions → FORMULAS / metrics / ratios / counts, GROUPED BY FAMILY.
    Created as kind='instruction', load_mode='intelligent' → keyword-ranked and
    retrievable in ALL modes (chat, deep analysis, training).
  - skills → genuine long multi-step PROCEDURES / SOPs / runbooks. Created as
    kind='skill'. Most analytics overviews have ZERO.

★WHY the split still matters (the retrieval mechanics differ):
kind='skill' instructions are PULL-ON-DEMAND via the `read_instruction` tool.
They are advertised as a title + description and their body arrives only if the
agent chooses to fetch it. Normal instructions with load_mode='always'
(force-loaded) or load_mode='intelligent' (keyword-ranked, kind!='skill') are
delivered without the agent having to ask.

So a metric FORMULA still belongs in an intelligent instruction: a formula is
only useful when it is actually in front of the model, and a skill is only
present if the planner decided the one-line description looked relevant.

(Until 2026-07-26 `read_instruction` was allowed_modes=["chat"], which made
skills genuinely UNREACHABLE in deep analysis and training while still being
advertised there. That is fixed — it is now ["chat", "deep", "training"] — but
the pull-vs-push distinction above is the durable reason for the split.)

preview() performs NO database writes; apply()/undo() persist and reverse.

Gated by settings.instruction_improve (env INSTRUCTION_IMPROVE); the route 403s
when the flag is off, so this module is dormant by default.
"""

import json
import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.instruction import Instruction
from app.models.organization import Organization
from app.models.user import User
from app.ai.llm import LLM
from app.dependencies import async_session_maker

logger = logging.getLogger(__name__)


class InstructionImproveService:

    async def _load_instruction(
        self, db: AsyncSession, instruction_id: str, organization: Organization
    ) -> Optional[Instruction]:
        """Load one instruction (with its data sources + references) scoped to the org."""
        q = await db.execute(
            select(Instruction)
            .options(
                selectinload(Instruction.data_sources),
                selectinload(Instruction.references),
            )
            .filter(
                Instruction.id == instruction_id,
                Instruction.organization_id == organization.id,
                Instruction.deleted_at.is_(None),
            )
        )
        return q.scalar_one_or_none()

    def _build_prompt(self, title: str, text: str, schema: str = "") -> str:
        # NOTE: schema is accepted for API compatibility but intentionally NOT
        # injected. A schema-aware "rewrite as overview" prompt was tried and made
        # the model DROP the formula section (metrics → 0). This focused split
        # prompt reliably separates meanings from formulas. Overview @mention
        # formatting is a separate concern (CRM's tables are UUID-named anyway).
        #
        # ★Retrieval-aware split: formulas MUST go to metric_instructions (created
        # as intelligent instructions, reachable in deep analysis + training).
        # Skills are reachable in CHAT ONLY, so reserve them for genuine
        # interactive multi-step procedures — most overviews have none.
        return f"""You are reorganizing an AI analytics agent's reference instruction.

Instruction title: {title}

Current instruction text:
\"\"\"
{text}
\"\"\"

This single instruction is loaded on EVERY query, which is wasteful and hard to
parse. Split it into THREE kinds of content:

1. DICTIONARY (stays always-loaded on every query): column/term meanings, table
   descriptions, relationships & joins, naming conventions and quirks. Keep it
   clean and factual. Preserve any @TableName mentions exactly as written. Do NOT
   include calculation formulas or metric definitions here.

2. METRIC_INSTRUCTIONS (retrievable in every mode, incl. deep analysis): the
   FORMULAS — every calculation, metric, ratio, count, percentage or aggregation
   (e.g. "% Retained Users", "Lead by Channel Type", "NU Trade/ETH/Online").
   GROUP related formulas into a SMALL number of families (aim for 2-5 groups,
   NOT one per metric). Examples of families: all rate/percentage metrics
   together; all segment counts together; all channel-volume counts together.
   Each group = one item with:
     - title: the family name (e.g. "Rate metrics", "Segment counts")
     - description: one line saying what the family covers
     - body: ALL the grouped formulas, each with its full filter criteria
   For any metric expressed as a rate or percentage, the body MUST state BOTH the
   numerator AND the denominator plus the formula. If the source text is missing
   the denominator (or numerator) for such a metric, DO NOT invent it — instead
   add a "warnings" array to that item noting exactly what is missing.

3. SKILLS (loaded on demand, CHAT ONLY): reserve these ONLY for genuine long
   multi-step PROCEDURES / SOPs / runbooks meant for interactive use (a sequence
   of steps a human walks through). A plain formula or metric is NOT a skill — it
   belongs in metric_instructions. Most overviews will have ZERO skills; that is
   normal and correct. Each skill has title, one-line description, and body, and
   may carry an optional "warnings" array.

Rules:
- Classify each non-dictionary item: is it a FORMULA (→ metric_instructions,
  grouped by family) or a multi-step PROCEDURE (→ skills)? When in doubt, it is a
  FORMULA — skills are the rare exception.
- Never invent content. Only reorganize what is already in the text. Never invent
  a missing numerator/denominator — flag it in "warnings" instead.
- Never DROP content: every calculation/metric in the original MUST appear in
  some metric_instructions family (or a skill), and every meaning MUST appear in
  the dictionary.
- Preserve @mentions verbatim wherever they appear.
- If there are no formulas, return an empty metric_instructions array; if there
  are no procedures, return an empty skills array; put all descriptive content in
  dictionary_text.

Return STRICT JSON only, no markdown:
{{"dictionary_text": "...", "metric_instructions": [{{"title": "...", "description": "...", "body": "...", "warnings": ["..."]}}], "skills": [{{"title": "...", "description": "...", "body": "...", "warnings": ["..."]}}]}}"""

    def _likely_has_formulas(self, text: str) -> bool:
        """Heuristic: does the source text contain calculation/metric language that
        should split into skills? Drives retry-on-empty."""
        t = (text or "").lower()
        signals = [
            "%", "÷", "divided by", "number of", "count of", " rate", "ratio",
            "percentage", "retained", "recruit", "per serving", "per day",
            "per month", "sum of", "average", "total number",
        ]
        hits = sum(1 for s in signals if s in t)
        return hits >= 2

    def _parse_llm_json(self, response: str) -> dict:
        response = (response or "").strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        return json.loads(response.strip())

    def _clean_items(self, raw_items) -> list:
        """Normalize a list of {title, description, body, warnings?} items,
        dropping any without both a title and a body."""
        out = []
        for s in (raw_items or []):
            if not isinstance(s, dict):
                continue
            title = (s.get("title") or "").strip()
            body = (s.get("body") or "").strip()
            if not title or not body:
                continue
            warnings = [
                str(w).strip()
                for w in (s.get("warnings") or [])
                if str(w).strip()
            ]
            out.append({
                "title": title,
                "description": (s.get("description") or "").strip(),
                "body": body,
                "warnings": warnings,
            })
        return out

    async def preview(
        self,
        db: AsyncSession,
        instruction_id: str,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Dry-run: propose a split. NO database writes."""
        instruction = await self._load_instruction(db, instruction_id, organization)
        if instruction is None:
            raise ValueError("Instruction not found")

        text = (instruction.text or "").strip()
        if not text:
            raise ValueError("Instruction has no text to improve")

        title = (instruction.title or instruction.category or "OVERVIEW").strip()

        # LLM split — offload the sync inference call off the event loop, matching
        # the pattern used by generate_datasource_instruction.
        model = await organization.get_default_llm_model(db)
        llm = LLM(model, usage_session_maker=async_session_maker)
        prompt = self._build_prompt(title, text)

        def _run():
            return llm.inference(prompt, usage_scope="instruction.improve.preview")

        # The default model is non-deterministic and sometimes returns the clean
        # dictionary but EMPTY formula buckets — silently dropping the formula
        # section. When the source clearly contains formulas/metrics, retry a few
        # times until we actually get formula content back. "Success" = got any
        # metric_instructions OR skills. Keep the best (most items) run.
        expects_formulas = self._likely_has_formulas(text)
        best_parsed = None
        best_metrics, best_skills = [], []
        best_count = -1
        max_attempts = 3 if expects_formulas else 1
        for attempt in range(max_attempts):
            raw = await asyncio.to_thread(_run)
            try:
                parsed = self._parse_llm_json(raw)
            except Exception as e:
                logger.warning("instruction.improve preview: JSON parse failed (attempt %s): %s", attempt + 1, e)
                continue
            # Backward compat: old shape has only "skills". Missing keys → [].
            metrics_in = self._clean_items(parsed.get("metric_instructions"))
            skills_in = self._clean_items(parsed.get("skills"))
            total = len(metrics_in) + len(skills_in)
            if best_parsed is None or total > best_count:
                best_parsed, best_metrics, best_skills, best_count = parsed, metrics_in, skills_in, total
            if total > 0 or not expects_formulas:
                break  # got formula content (or none expected) → stop retrying

        if best_parsed is None:
            raise ValueError("Could not parse the improvement proposal")

        dictionary_text = (best_parsed.get("dictionary_text") or "").strip()
        metric_instructions = best_metrics
        skills = best_skills

        # Suggested references = the datasource_table objects on this instruction's
        # data source(s) that the user can access. Reuses the existing helper so
        # access rules stay identical. Read-only.
        suggested_refs = []
        ds_ids = [str(ds.id) for ds in (instruction.data_sources or [])]
        if ds_ids:
            from app.services.instruction_service import InstructionService
            try:
                refs = await InstructionService().get_available_references(
                    db=db,
                    organization=organization,
                    current_user=current_user,
                    q=None,
                    types="datasource_table",
                    data_source_ids=",".join(ds_ids),
                )
                for r in refs or []:
                    suggested_refs.append({
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "data_source_id": r.get("data_source_id"),
                    })
            except Exception as e:
                logger.warning("instruction.improve preview: suggested refs failed: %s", e)

        warnings_total = sum(len(i.get("warnings") or []) for i in metric_instructions) \
            + sum(len(i.get("warnings") or []) for i in skills)

        return {
            "instruction_id": str(instruction.id),
            "title": title,
            "original_text": text,
            "dictionary_text": dictionary_text,
            "metric_instructions": metric_instructions,
            "skills": skills,
            "suggested_refs": suggested_refs,
            "counts": {
                "metric_instructions": len(metric_instructions),
                "skills": len(skills),
                "suggested_refs": len(suggested_refs),
                "warnings": warnings_total,
            },
        }

    # ── Apply / Undo (P2) ────────────────────────────────────────────────────

    def _refs_to_create_dicts(self, refs) -> list:
        """Snapshot an instruction's existing references as plain dicts that can
        rebuild them 1:1 via replace_for_instruction."""
        out = []
        for r in refs or []:
            out.append({
                "object_type": r.object_type,
                "object_id": r.object_id,
                "column_name": getattr(r, "column_name", None),
                "relation_type": getattr(r, "relation_type", None),
                "display_text": getattr(r, "display_text", None),
            })
        return out

    async def apply(
        self,
        db: AsyncSession,
        instruction_id: str,
        organization: Organization,
        current_user: User,
        payload: dict,
    ) -> dict:
        """Persist a split: rewrite the primary instruction to the lean
        dictionary, create the metric families as kind='instruction'
        load_mode='intelligent' rows (reachable in deep analysis) and any genuine
        procedures as kind='skill' rows, and attach the chosen table references —
        all tagged with one batch id and with the original state snapshotted into
        structured_data['improve_backup'] for a clean undo.

        Non-destructive: nothing is hard-deleted; the created rows are new and the
        original text + reference set are recoverable via undo()."""
        import uuid
        from app.schemas.instruction_schema import InstructionCreate
        from app.schemas.instruction_reference_schema import InstructionReferenceCreate
        from app.services.instruction_service import InstructionService
        from app.services.instruction_reference_service import InstructionReferenceService

        instruction = await self._load_instruction(db, instruction_id, organization)
        if instruction is None:
            raise ValueError("Instruction not found")

        dictionary_text = (payload.get("dictionary_text") or "").strip()
        if not dictionary_text:
            raise ValueError("dictionary_text is required to apply")
        # New shape has metric_instructions + skills; old shape has only skills.
        # Missing keys default to [] so either round-trips without crashing.
        metrics_in = payload.get("metric_instructions") or []
        skills_in = payload.get("skills") or []
        suggested_refs = payload.get("suggested_refs") or []

        # Guard against double-apply on an already-improved instruction.
        sd = dict(instruction.structured_data or {})
        if sd.get("improve_backup"):
            raise ValueError("This instruction was already improved. Undo first to re-apply.")

        ds_ids = [str(ds.id) for ds in (instruction.data_sources or [])]
        batch_id = uuid.uuid4().hex
        original_text = instruction.text or ""
        original_references = self._refs_to_create_dicts(instruction.references)

        instr_service = InstructionService()
        ref_service = InstructionReferenceService()

        ai_source = f"improve:{batch_id}"[:50]

        async def _create_rows(items, kind, load_mode):
            """Create a batch of instruction rows of one kind; return their ids."""
            created_ids = []
            for s in items:
                if not isinstance(s, dict):
                    continue
                body = (s.get("body") or "").strip()
                ttl = (s.get("title") or "").strip()
                if not body or not ttl:
                    continue
                create = InstructionCreate(
                    text=body,
                    title=ttl[:255],
                    description=(s.get("description") or "").strip() or None,
                    kind=kind,
                    category="general",
                    status="published",
                    load_mode=load_mode,
                    ai_source=ai_source,
                    data_source_ids=ds_ids,
                )
                created = await instr_service.create_instruction(
                    db, create, current_user, organization, force_global=False
                )
                if created is not None and getattr(created, "id", None):
                    created_ids.append(str(created.id))
            return created_ids

        # 1a) Metric families → kind='instruction', load_mode='intelligent'.
        #     ★These are keyword-ranked and reachable in deep analysis + training,
        #     which is exactly where formulas must be available.
        created_metric_ids = await _create_rows(
            metrics_in, kind="instruction", load_mode="intelligent"
        )

        # 1b) Skills → kind='skill' (pull-on-demand procedures, all modes).
        created_skill_ids = await _create_rows(
            skills_in, kind="skill", load_mode="intelligent"
        )

        # 2) Attach references: keep existing + add the chosen datasource_table
        #    refs (deduped). replace_for_instruction rewrites the whole set.
        existing_obj_ids = {(r.object_type, r.object_id) for r in (instruction.references or [])}
        new_ref_creates = [InstructionReferenceCreate(**d) for d in original_references]
        added_reference_object_ids = []
        for r in suggested_refs:
            rid = r.get("id") if isinstance(r, dict) else None
            if not rid:
                continue
            key = ("datasource_table", rid)
            if key in existing_obj_ids:
                continue
            new_ref_creates.append(InstructionReferenceCreate(
                object_type="datasource_table",
                object_id=rid,
                relation_type="scope",
                display_text=(r.get("name") if isinstance(r, dict) else None),
            ))
            added_reference_object_ids.append(rid)
            existing_obj_ids.add(key)

        if added_reference_object_ids:
            await ref_service.replace_for_instruction(
                db, instruction_id, new_ref_creates, organization, data_source_ids=ds_ids
            )

        # 3) Rewrite the primary instruction to the lean dictionary + snapshot for undo.
        instruction.text = dictionary_text
        sd["improve_backup"] = {
            "batch_id": batch_id,
            "ai_source": ai_source,
            "original_text": original_text,
            "original_references": original_references,
            "created_metric_ids": created_metric_ids,
            "created_skill_ids": created_skill_ids,
            "added_reference_object_ids": added_reference_object_ids,
        }
        instruction.structured_data = sd

        await db.commit()
        await db.refresh(instruction)

        return {
            "instruction_id": str(instruction.id),
            "batch_id": batch_id,
            "created_metric_ids": created_metric_ids,
            "created_skill_ids": created_skill_ids,
            "added_reference_count": len(added_reference_object_ids),
            "dictionary_chars": len(dictionary_text),
        }

    async def undo(
        self,
        db: AsyncSession,
        instruction_id: str,
        organization: Organization,
        current_user: User,
    ) -> dict:
        """Reverse an apply(): restore the original text + reference set and
        soft-delete the skills that apply() created. Idempotent-safe: raises if
        there is nothing to undo."""
        from app.schemas.instruction_reference_schema import InstructionReferenceCreate
        from app.services.instruction_service import InstructionService
        from app.services.instruction_reference_service import InstructionReferenceService

        instruction = await self._load_instruction(db, instruction_id, organization)
        if instruction is None:
            raise ValueError("Instruction not found")

        sd = dict(instruction.structured_data or {})
        backup = sd.get("improve_backup")
        if not backup:
            raise ValueError("Nothing to undo — this instruction was not improved")

        ds_ids = [str(ds.id) for ds in (instruction.data_sources or [])]
        instr_service = InstructionService()
        ref_service = InstructionReferenceService()

        # 1) Restore the original reference set.
        orig_refs = backup.get("original_references") or []
        try:
            ref_creates = [InstructionReferenceCreate(**d) for d in orig_refs]
            await ref_service.replace_for_instruction(
                db, instruction_id, ref_creates, organization, data_source_ids=ds_ids
            )
        except Exception as e:
            logger.warning("instruction.improve undo: restore refs failed: %s", e)

        # 2) Soft-delete every row apply() created — both metric instructions and
        #    skills. Collect the recorded ids from both buckets, then robustly
        #    sweep by ai_source (improve:{batch_id}) to catch any row not captured
        #    in the id lists (partial-write safety). Backward compat: old backups
        #    have only created_skill_ids and no ai_source.
        ids_to_remove = set(backup.get("created_metric_ids") or [])
        ids_to_remove.update(backup.get("created_skill_ids") or [])

        ai_source = backup.get("ai_source")
        if not ai_source:
            bid = backup.get("batch_id")
            ai_source = f"improve:{bid}"[:50] if bid else None
        if ai_source:
            try:
                q = await db.execute(
                    select(Instruction.id).filter(
                        Instruction.ai_source == ai_source,
                        Instruction.organization_id == organization.id,
                        Instruction.deleted_at.is_(None),
                    )
                )
                for (iid,) in q.all():
                    ids_to_remove.add(str(iid))
            except Exception as e:
                logger.warning("instruction.improve undo: ai_source sweep failed: %s", e)

        removed = 0
        for iid in ids_to_remove:
            try:
                ok = await instr_service.delete_instruction(db, iid, organization, current_user)
                if ok:
                    removed += 1
            except Exception as e:
                logger.warning("instruction.improve undo: delete %s failed: %s", iid, e)

        # 3) Restore original text + clear the backup marker.
        instruction.text = backup.get("original_text", instruction.text)
        sd.pop("improve_backup", None)
        instruction.structured_data = sd

        await db.commit()
        await db.refresh(instruction)

        return {
            "instruction_id": str(instruction.id),
            "restored_text_chars": len(instruction.text or ""),
            "removed_instructions": removed,
            # legacy key kept for any FE that reads it
            "removed_skills": removed,
        }


instruction_improve_service = InstructionImproveService()
