.PHONY: ci ci-full

ci:
	bash scripts/ci-local.sh

ci-full:
	bash scripts/ci-local.sh --full
