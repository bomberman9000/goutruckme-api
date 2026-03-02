.PHONY: deploy up-v2 scale-workers clear-stream queue-stat logs-workers

.DEFAULT_GOAL := deploy

up-v2:
	docker compose --profile parser-v2 up -d

scale-workers:
	docker compose --profile parser-v2 up -d --scale parser-worker=3 parser-worker

clear-stream:
	docker compose exec -T redis redis-cli del logistics_stream

queue-stat:
	docker compose exec -T redis redis-cli xlen logistics_stream

logs-workers:
	docker compose --profile parser-v2 logs -f parser-worker

deploy:
	git stash
	git pull
	docker compose --profile parser-v2 build --no-cache
	docker compose --profile parser-v2 up -d --force-recreate
	docker compose ps
