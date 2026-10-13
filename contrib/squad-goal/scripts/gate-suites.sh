#!/bin/bash
# usage: <changed file names, one per line> | gate-suites.sh   Prints the fork-gate suite set a diff needs: full | shell | python.
# The one table (path regex -> suite), first match wins. A diff confined to one suite's paths runs that suite; a diff touching two
# suites, a path the table does not know, or no paths at all (any doubt) runs the full gate.
table() { cat <<'T'
^contrib/squad-goal/	shell
^tests/test_gui_test_	full
^dashboard/	python
^tests/	python
T
}
want=""
while IFS= read -r f; do [ -n "$f" ] || continue; s=full
  while IFS=$'\t' read -r re suite; do [[ "$f" =~ $re ]] && { s=$suite; break; }; done < <(table)
  [ -z "$want" ] && want=$s; [ "$want" = "$s" ] || want=full
done
echo "${want:-full}"
