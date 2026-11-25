.PHONY: ci ci-full ci-evidence

ci:
	bash scripts/ci-local.sh

ci-full:
	bash scripts/ci-local.sh --full

ci-evidence:
	bash scripts/ci-evidence.sh
