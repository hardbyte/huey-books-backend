FROM postgres:18 AS extension-build

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential ca-certificates curl postgresql-server-dev-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN curl --fail --location --silent --show-error \
      https://github.com/timescale/pg_textsearch/releases/download/v1.3.1/pg_textsearch-1.3.1.tar.gz \
      --output /tmp/pg_textsearch.tar.gz \
    && echo "5d49f047c7e926a99f863d8af9d7800b9989ebb84c42f827ad81dac746a0b9e8  /tmp/pg_textsearch.tar.gz" | sha256sum --check \
    && tar --extract --gzip --file /tmp/pg_textsearch.tar.gz --strip-components=1 \
    && make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config \
    && make PG_CONFIG=/usr/lib/postgresql/18/bin/pg_config DESTDIR=/install install

FROM postgres:18
COPY --from=extension-build /install/ /
CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
