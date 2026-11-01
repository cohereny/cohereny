#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE cohereny_dev;
    CREATE DATABASE cohereny_test;
    CREATE DATABASE cohereny_prod;
    GRANT ALL PRIVILEGES ON DATABASE cohereny_dev TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE cohereny_test TO $POSTGRES_USER;
    GRANT ALL PRIVILEGES ON DATABASE cohereny_prod TO $POSTGRES_USER;
EOSQL
