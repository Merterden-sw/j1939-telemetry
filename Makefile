# ---------------------------------------------------------------------------
# Gelistirme kisayollari
# ---------------------------------------------------------------------------
.PHONY: help up down logs build test lint fmt dev-backend dev-frontend clean

help:           ## Komut listesi
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:             ## Tum ortami ayaga kaldir (http://localhost:8080)
	docker compose up --build

down:           ## Ortami kapat
	docker compose down -v

logs:           ## Konteyner kayitlarini izle
	docker compose logs -f

build:          ## Imajlari derle
	docker compose build

test:           ## Backend testlerini calistir
	cd backend && python -m pytest -v

lint:           ## Statik analiz
	cd backend && ruff check . && ruff format --check .
	cd frontend && for f in js/*.js; do node --check $$f; done

fmt:            ## Kodu bicimle
	cd backend && ruff check --fix . && ruff format .

dev-backend:    ## Backend'i yerelde calistir (canli yeniden yukleme)
	cd backend && uvicorn app.main:app --reload --port 8000

dev-frontend:   ## Arayuzu yerelde servis et (backend 8000 portunda olmali)
	cd frontend && python3 -m http.server 8081

clean:          ## Gecici dosyalari sil
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/test-results.xml
