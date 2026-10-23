#!/usr/bin/env bash
# Sources every tests/test_*.sh and runs all functions named test_*.
# Prints PASS/FAIL per test; exits 1 if any failed.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PC_TESTS_DIR="$HERE" PC_SCRIPTS_DIR="$HERE/../scripts"
PC_TMP=$(mktemp -d); trap 'rm -rf "$PC_TMP"' EXIT
export XDG_CONFIG_HOME="$PC_TMP/config" XDG_STATE_HOME="$PC_TMP/state" HOME="$PC_TMP/home"
fail=0 total=0
for f in "$HERE"/test_*.sh; do
  [[ -e "$f" ]] || continue
  before=$(declare -F | awk '{print $3}' | grep '^test_' | sort)
  # shellcheck disable=SC1090
  source "$f"
  after=$(declare -F | awk '{print $3}' | grep '^test_' | sort)
  for t in $(comm -13 <(echo "$before") <(echo "$after")); do
    total=$((total + 1))
    out=$( (set -e; "$t") 2>&1 ); rc=$?
    if [[ $rc -eq 0 ]]; then
      echo "PASS $t"
    else
      echo "FAIL $t"; echo "$out" | sed 's/^/    /'; fail=$((fail + 1))
    fi
  done
done
echo "$((total - fail))/$total passed"
[[ $fail -eq 0 ]]
