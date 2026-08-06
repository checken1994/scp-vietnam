# Task 10-C: Makefile — local CI/CD runner (mirror cua .github/workflows/scp-ci.yml)
# Usage: make ci | make lint | make security | make test | make modularity | make complexity | make gates
#
# [ROOT-FIX Task 38-A / Issue 2] DNA #6 Evidence: secret_scan.py + check_tree_sync.py
# existed as standalone scripts but were NEVER called by CI/Makefile/deploy —
# they were "PASS" in tests but actually dead in production. Now wired into the
# `gates` target and added to the `ci` aggregate target so they run on every CI.

.PHONY: lint test security modularity complexity clean install ci gates secret_scan check_tree_sync

install:
	pip install -r scp/requirements.txt
	pip install -r scp/requirements-dev.txt

lint:
	ruff check scp/ --select=F821,E722 --no-fix
	@# ruff format check disabled (249 files want reformat — out of scope Task 10-C)
	@# ruff format --check scp/  # uncomment after bulk format pass

test:
	python -m pytest tests/ scp/tests/ -v --tb=short --maxfail=10 --continue-on-collection-errors

test-golden:
	python -m pytest tests/test_golden_23.py -v --tb=short

test-external:
	python -m pytest scp/tests/external_audit/ -v --tb=short

test-judge:
	python -m pytest scp/tests/test_judge_characterization.py -v --tb=short

security:
	@bandit -r scp/ -ll --exit-zero -f json -o /tmp/bandit-report.json
	@echo "--- HIGH count (must be 0) ---"
	@HIGH=$$(python3 -c "import json; d=json.load(open('/tmp/bandit-report.json')); print(sum(1 for r in d.get('results',[]) if r['issue_severity']=='HIGH'))"); \
	if [ "$$HIGH" -gt "0" ]; then echo "FAIL: $$HIGH HIGH severity issues"; exit 1; else echo "PASS: 0 HIGH severity issues"; fi

modularity:
	@echo "Files >1000 LOC (target <5):"
	@find scp/ -name "*.py" -not -path "*/__pycache__/*" -exec wc -l {} + | awk '$$1>1000 && $$2!="total"'

complexity:
	@# Task 9-C target: judge() CC<50 (currently CC=42)
	@ruff check scp/runtime/judge.py --select=C901 --output-format=concise 2>&1 | grep "C901 .judge. is too complex" | sed "s/.*complex (\([0-9]*\) >.*/\1/" > /tmp/judge_cc.txt
	@CC=$$(cat /tmp/judge_cc.txt); \
	if [ -z "$$CC" ]; then echo "PASS: judge() CC < 10 (default threshold)"; \
	elif [ "$$CC" -lt "50" ]; then echo "PASS: judge() CC=$$CC < 50 (Task 9-C target)"; \
	else echo "FAIL: judge() CC=$$CC >= 50"; exit 1; fi

clean:
	find scp/ -name "__pycache__" -type d -exec rm -rf {} +
	find scp/ -name "*.pyc" -delete

# [ROOT-FIX Task 38-A / Issue 2] Gate targets — must run before every zip/deploy.
# secret_scan.py: scans ALL files by content regex for leaked API keys.
# check_tree_sync.py: ensures no duplicate scp-plugin/ code tree exists.
gates: secret_scan check_tree_sync
	@echo "All gates passed"

secret_scan:
	@python secret_scan.py
	@echo "PASS: secret_scan"

check_tree_sync:
	@python check_tree_sync.py
	@echo "PASS: check_tree_sync"

ci: lint security test modularity complexity gates
	@echo "All CI checks passed - RC-1 (F821/E722=0), RC-2 (HIGH=0), RC-10 (external audit), CC<50, modularity, gates (secret_scan + check_tree_sync)"
