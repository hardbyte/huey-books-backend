FROM mcr.microsoft.com/playwright:v1.57.0-noble
WORKDIR /verify
COPY e2e-live/package.json e2e-live/package-lock.json ./
RUN npm ci --ignore-scripts
COPY e2e-live/ ./
ENTRYPOINT ["node", "/verify/verify.mjs"]
