#!/usr/bin/env bash
# Pre-session regression: run before every exec session. Green = go.
set -uo pipefail
B=https://paxtonprospect-bridge-ops-229567506693.us-east4.run.app
IDTOK=$(gcloud auth print-identity-token)
H="Authorization: Bearer $IDTOK"
PASS=0; FAIL=0
check() { # name, ok_bool
  if [ "$2" = "True" ] || [ "$2" = "true" ]; then echo "  ✓ $1"; PASS=$((PASS+1));
  else echo "  ✗ $1"; FAIL=$((FAIL+1)); fi
}

echo "== sims health =="
for s in crm-sim newswire content-hub market-data kyc directory scheduler mailpit; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "$H" \
    "https://paxtonprospect-$s-229567506693.us-east4.run.app/health" 2>/dev/null)
  [ "$s" = "mailpit" ] && CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "$H" \
    "https://paxtonprospect-mailpit-229567506693.us-east4.run.app/sim/mailpit/api/v1/messages?limit=1")
  check "$s [$CODE]" $([ "$CODE" = "200" ] && echo true || echo false)
done

echo "== J4 tripwire battery =="
OK=$(curl -s -X POST -H "$H" "$B/test/j4?runs=10" | python3 -c "import json,sys; print(json.load(sys.stdin)['ok'])")
check "j4 10/10 trips" "$OK"

echo "== G3 journey checks (J7 gate / J8 coach / J5 handoff) =="
OK=$(curl -s -X POST -H "$H" "$B/test/g3" --max-time 400 | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['ok']); [print('    ', k, v) for k,v in d['checks'].items()]")
check "g3 all checks" "$(echo "$OK" | head -1)"
echo "$OK" | tail -n +2

echo "== G2 live-journey run (4 min) =="
OK=$(curl -s -X POST -H "$H" "$B/test/g2?minutes=4&room_id=regression" --max-time 330 | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['ok'])")
check "g2 all checks" "$OK"

echo; echo "REGRESSION: $PASS passed, $FAIL failed $([ $FAIL -eq 0 ] && echo '— GO' || echo '— NO-GO: check traces')"
exit $FAIL
