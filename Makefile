.PHONY: builder-up builder-info builder-stop package image image-stock stock-check tanks-deps tanks tanks-test game-deps game game-test

APP ?= status
GAME_BACKEND ?= desktop
STOCK_LOCK ?= image/stock-image.json

builder-up:
	python3 scripts/builder up

builder-info:
	python3 scripts/builder info

builder-stop:
	python3 scripts/builder stop

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
