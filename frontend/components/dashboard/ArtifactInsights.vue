<template>
  <!-- Grounded narrative above the dashboard. Renders nothing at all when the
       artifact carries no insights (older artifacts, or generation disabled) —
       an empty shell would be worse than no panel. -->
  <section
    v-if="hasInsights"
    class="flex-shrink-0 border-b border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-900 px-4 py-3"
  >
    <button
      type="button"
      class="w-full flex items-start gap-2.5 text-left rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500/60 focus-visible:ring-offset-1 focus-visible:ring-offset-white dark:focus-visible:ring-offset-gray-900"
      :aria-expanded="isOpen"
      :aria-controls="panelId"
      @click="isOpen = !isOpen"
    >
      <UIcon
        name="i-heroicons-chevron-right-20-solid"
        class="w-4 h-4 mt-1 flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-150"
        :class="isOpen ? 'rotate-90' : ''"
      />
      <span class="min-w-0 flex-1">
        <span class="flex items-center gap-1.5">
          <UIcon name="i-heroicons-sparkles" class="w-3 h-3 flex-shrink-0 text-cyan-500" />
          <span class="text-[10px] font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">
            {{ t('artifactFrame.insights.label') }}
          </span>
        </span>
        <span
          v-if="headline"
          class="block mt-1 text-sm sm:text-base font-semibold leading-snug text-gray-900 dark:text-gray-100 break-words"
        >
          {{ headline }}
        </span>
      </span>
      <span class="sr-only">
        {{ isOpen ? t('artifactFrame.insights.collapse') : t('artifactFrame.insights.expand') }}
      </span>
    </button>

    <div v-show="isOpen" :id="panelId" class="ps-6 pe-1">
      <ul v-if="findings.length" class="mt-2.5 space-y-1.5">
        <li
          v-for="finding in findings"
          :key="finding.key"
          class="flex items-start gap-2 text-xs sm:text-[13px] leading-relaxed text-gray-600 dark:text-gray-300"
        >
          <span class="mt-[7px] w-1 h-1 rounded-full flex-shrink-0 bg-gray-300 dark:bg-gray-600" />
          <span class="min-w-0 break-words"><template v-for="(seg, si) in finding.segments" :key="si"><span v-if="seg.figure" class="font-mono text-[0.92em] px-1 py-px rounded bg-cyan-50 dark:bg-cyan-900/30 text-cyan-700 dark:text-cyan-300">{{ seg.text }}</span><template v-else>{{ seg.text }}</template></template><span
              v-if="finding.source"
              class="ms-1.5 inline-flex items-center gap-0.5 align-middle text-[10px] text-gray-400 dark:text-gray-500"
            >
              <UIcon name="i-heroicons-chart-bar-square" class="w-2.5 h-2.5 flex-shrink-0" />
              <span class="truncate max-w-[14rem]">{{ finding.source }}</span>
            </span></span>
        </li>
      </ul>

      <!-- ★Dropped findings are stated, not hidden. A summary that lost four of
           five points is not a short summary, it is a warning — and a reader who
           cannot see that has no way to know the panel is thin because the
           figures failed verification rather than because there was little to
           say. -->
      <p
        v-if="rejectedCount > 0"
        class="mt-2 text-[10px] text-amber-600 dark:text-amber-400"
      >
        {{ t('artifactFrame.insights.rejected', { count: rejectedCount }) }}
      </p>

      <p v-if="generatedAtLabel" class="mt-2 text-[10px] text-gray-400 dark:text-gray-500">
        {{ t('artifactFrame.insights.generatedAt', { time: generatedAtLabel }) }}
      </p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue';

interface InsightFinding {
  text?: string;
  figures?: string[];
  viz_id?: string;
}

interface ArtifactInsightsPayload {
  headline?: string;
  findings?: InsightFinding[];
  // How many findings were dropped for citing a figure absent from the data.
  rejected_count?: number;
  generated_at?: string;
}

const props = defineProps<{
  // The artifact's `content.insights` block. May be absent or null.
  insights?: ArtifactInsightsPayload | null;
  // Used only to key the per-artifact collapse preference.
  artifactId?: string | null;
  // Optional: lets a finding name the visualization its figures came from.
  visualizations?: any[];
}>();

const { t } = useI18n();
const _df = useFormatDate();

const headline = computed(() => String(props.insights?.headline || '').trim());

type Segment = { text: string; figure: boolean };

// Split a finding into plain/figure segments so each number can be styled
// without ever putting model-authored text through v-html.
function buildSegments(text: string, figures?: string[]): Segment[] {
  const clean = String(text || '');
  if (!clean) return [];

  // Longest first: "48.8%" must win over a bare "48" it contains.
  const needles = (Array.isArray(figures) ? figures : [])
    .map((f) => String(f ?? '').trim())
    .filter((f) => f.length > 0)
    .sort((a, b) => b.length - a.length);

  if (!needles.length) return [{ text: clean, figure: false }];

  const marks: Array<[number, number]> = [];
  for (const needle of needles) {
    let from = 0;
    for (;;) {
      const at = clean.indexOf(needle, from);
      if (at === -1) break;
      const end = at + needle.length;
      // Skip anything overlapping an already-claimed span.
      if (!marks.some(([s, e]) => at < e && end > s)) marks.push([at, end]);
      from = end;
    }
  }

  if (!marks.length) return [{ text: clean, figure: false }];
  marks.sort((a, b) => a[0] - b[0]);

  const segments: Segment[] = [];
  let cursor = 0;
  for (const [start, end] of marks) {
    if (start > cursor) segments.push({ text: clean.slice(cursor, start), figure: false });
    segments.push({ text: clean.slice(start, end), figure: true });
    cursor = end;
  }
  if (cursor < clean.length) segments.push({ text: clean.slice(cursor), figure: false });
  return segments;
}

function vizTitle(vizId?: string): string {
  if (!vizId || !Array.isArray(props.visualizations)) return '';
  const match = props.visualizations.find((v: any) => String(v?.id) === String(vizId));
  return String(match?.title || '').trim();
}

const findings = computed(() => {
  const raw = Array.isArray(props.insights?.findings) ? props.insights!.findings! : [];
  return raw
    .map((f, i) => ({
      key: `${i}-${String(f?.viz_id || '')}`,
      segments: buildSegments(String(f?.text || ''), f?.figures),
      source: vizTitle(f?.viz_id),
    }))
    .filter((f) => f.segments.length > 0);
});

const rejectedCount = computed(() => {
  const n = Number(props.insights?.rejected_count ?? 0);
  return Number.isFinite(n) && n > 0 ? n : 0;
});

const hasInsights = computed(() => !!headline.value || findings.value.length > 0 || rejectedCount.value > 0);

const generatedAtLabel = computed(() => {
  const at = props.insights?.generated_at;
  if (!at) return '';
  try {
    return _df.formatDate(at as any);
  } catch {
    return '';
  }
});

// Deterministic id (no hydration mismatch, no random suffix).
const panelId = computed(() => `artifact-insights-${props.artifactId || 'panel'}`);

// Collapse state: open by default, remembered per artifact.
// Key style follows the existing `bow.learnAfterSave.<dsId>` precedent.
const storageKey = computed(() => (props.artifactId ? `bow.insightsOpen.${props.artifactId}` : ''));
const isOpen = ref(true);

watch(
  storageKey,
  (key) => {
    isOpen.value = true;
    if (!key || typeof window === 'undefined') return;
    try {
      const saved = window.localStorage.getItem(key);
      if (saved !== null) isOpen.value = saved === 'true';
    } catch {
      // storage unavailable (private mode) — keep the default
    }
  },
  { immediate: true },
);

watch(isOpen, (val) => {
  const key = storageKey.value;
  if (!key || typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(key, String(val));
  } catch {
    // ignore
  }
});
</script>
