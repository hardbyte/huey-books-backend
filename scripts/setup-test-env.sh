#!/usr/bin/env bash

export SQLALCHEMY_DATABASE_URI=postgresql://postgres:password@127.0.0.1:5432/postgres
export SQLALCHEMY_ASYNC_URI=postgresql+asyncpg://postgres:password@127.0.0.1:5432/postgres
export POSTGRESQL_PASSWORD=password
export SECRET_KEY=synthetic-unit-test-key
export SENDGRID_API_KEY=unused
export SHOPIFY_HMAC_SECRET=unused
export SLACK_BOT_TOKEN=unused
export OPENAI_API_KEY=unused
export WRIVETED_INTERNAL_API=http://127.0.0.1:8888
