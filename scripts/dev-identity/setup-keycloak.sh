#!/usr/bin/env bash
# Build the test realm. Idempotent — safe to re-run.
#
# ★The point of this file is the DIFFERENCES between the users. Four of the
# five exist to reproduce one refusal branch each, so that
# `_link_refusal_cause` is proved against a real identity provider rather than
# a fabricated claims dict:
#
#   verified       links cleanly
#   unverified     emailVerified=false  -> "your provider reports it unverified"
#   nomail         no email attribute   -> "your provider sent a username"
#   bothdoors      also exists in LDAP  -> merge, directory first
#   ssofirst       also exists in LDAP  -> merge, provider first
#
# DEVELOPMENT ONLY. See the compose file.
set -euo pipefail

KC="docker exec test-keycloak /opt/keycloak/bin/kcadm.sh"
REALM=citytest
CLIENT=dash-insights
SECRET=dash-test-secret
PW=KcPass123

echo "waiting for keycloak..."
for i in $(seq 1 60); do
  if $KC config credentials --server http://localhost:8080 --realm master \
       --user admin --password AdminKc123 >/dev/null 2>&1; then
    echo "  ready after ${i}s"; break
  fi
  sleep 1
done

$KC get "realms/$REALM" >/dev/null 2>&1 || $KC create realms -s realm=$REALM -s enabled=true
echo "realm $REALM"

if ! $KC get clients -r $REALM -q clientId=$CLIENT --fields id --format csv --noquotes 2>/dev/null | grep -q .; then
  $KC create clients -r $REALM \
    -s clientId=$CLIENT -s enabled=true -s protocol=openid-connect \
    -s publicClient=false -s secret=$SECRET \
    -s standardFlowEnabled=true -s directAccessGrantsEnabled=true \
    -s 'redirectUris=["http://localhost:8095/*","http://dash-app:3000/*"]' \
    -s 'webOrigins=["*"]' >/dev/null
fi
echo "client $CLIENT"

# ★★★Keycloak's realm profile REQUIRES email, firstName and lastName, and a
# user missing any of them cannot authenticate at all — the token endpoint
# answers `invalid_grant / "Account is not fully set up"`, which reads like a
# broken password rather than a schema rule. The `nomail` case exists precisely
# to have no email, so the requirement is lifted here (and the name is still
# supplied, or the same error returns for the other two fields).
python3 - <<'PYEOF'
import json, subprocess
raw = subprocess.run(["docker","exec","test-keycloak","/opt/keycloak/bin/kcadm.sh",
                      "get","users/profile","-r","citytest"],
                     capture_output=True, text=True).stdout
profile = json.loads(raw)
for attr in profile.get("attributes", []):
    if attr.get("name") == "email":
        attr.pop("required", None)
open("/tmp/profile.json","w").write(json.dumps(profile))
PYEOF
docker cp /tmp/profile.json test-keycloak:/tmp/profile.json >/dev/null
$KC update users/profile -r $REALM -f /tmp/profile.json >/dev/null
echo "email made optional (for the nomail case)"

mkuser() {   # username email emailVerified
  local u="$1" e="$2" v="$3"
  local id
  id=$($KC get users -r $REALM -q username="$u" --fields id --format csv --noquotes 2>/dev/null | head -1)
  if [ -n "$id" ]; then $KC delete "users/$id" -r $REALM >/dev/null 2>&1 || true; fi
  if [ "$e" = "-" ]; then
    $KC create users -r $REALM -s username="$u" -s enabled=true \
      -s firstName="No" -s lastName="Mail" >/dev/null
  else
    $KC create users -r $REALM -s username="$u" -s email="$e" \
      -s emailVerified=$v -s enabled=true -s firstName="Test" -s lastName="$u" >/dev/null
  fi
  id=$($KC get users -r $REALM -q username="$u" --fields id --format csv --noquotes | head -1)
  $KC set-password -r $REALM --username "$u" --new-password "$PW" >/dev/null
  printf '  %-12s email=%-28s emailVerified=%s\n' "$u" "$e" "$v"
}

echo "users:"
mkuser verified   verified@cityagent.io   true
mkuser unverified unverified@cityagent.io false
# ★★★The username is EMAIL-SHAPED and there is no email attribute — which is
# exactly what Entra and AD FS send as `upn`/`preferred_username`. A username
# that is not address-shaped never matches an existing account, so it can only
# ever create a new one and never exercises the gate.
mkuser upnonly@cityagent.io -           false
mkuser bothdoors  bothdoors@cityagent.io  true
mkuser ssofirst   ssofirst@cityagent.io   true
echo "done"
