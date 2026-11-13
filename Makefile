.PHONY: builder-up builder-info builder-stop package

builder-up:
	python3 scripts/builder up

builder-info:
	python3 scripts/builder info

builder-stop:
	python3 scripts/builder stop

package:
	python3 scripts/builder build
