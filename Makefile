.PHONY: builder-up builder-info builder-stop package image image-stock stock-check tanks-deps tanks tanks-test game-deps game game-test
.PHONY: zero-check zero-packages zero-session-package zero-power-policy-package zero-native-deps zero-native-wizard zero-integration

APP ?= status
GAME_BACKEND ?= desktop
STOCK_LOCK ?= image/stock-image.json

builder-up:
	python3 scripts/builder up

builder-info:
	python3 scripts/builder info

builder-stop:
	python3 scripts/builder stop

zero-check:
	python3 scripts/builder zero check

zero-native-deps:
	python3 builder/zero-integration.py deps

zero-native-wizard:
	python3 builder/zero-integration.py deps --check
	python3 scripts/builder zero build-wizard

zero-integration:
	test -n "$(WIZARD_ARTIFACTS)"
	python3 scripts/builder zero build-integration --wizard-artifacts "$(WIZARD_ARTIFACTS)"

zero-packages:
	python3 scripts/builder zero build-packages

zero-session-package:
	python3 scripts/builder zero build-packages --package session

zero-power-policy-package:
	python3 scripts/builder zero build-packages --package power

package:
	python3 scripts/builder build --app $(APP)

image:
	python3 scripts/builder image

image-stock:
	python3 scripts/builder image --stock-lock "$(STOCK_LOCK)"

stock-check:
	python3 image/stock-image.py check --lock "$(STOCK_LOCK)"

tanks-deps:
	python3 scripts/tanks deps

tanks:
	python3 scripts/tanks build

tanks-test:
	python3 scripts/tanks test

game-deps:
	python3 scripts/game deps --backend $(GAME_BACKEND)

game:
	python3 scripts/game build --backend $(GAME_BACKEND)

game-test:
	python3 scripts/game test --backend $(GAME_BACKEND)

.PHONY: chess chess-test chess-package
chess:
	python3 scripts/chess build --backend headless

chess-test:
	python3 scripts/chess test --backend headless

chess-package:
	python3 scripts/chess package
