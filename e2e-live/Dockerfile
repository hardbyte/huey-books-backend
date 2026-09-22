FROM mcr.microsoft.com/playwright:v1.57.0-noble
WORKDIR /verify
COPY e2e-live/package.json e2e-live/package-lock.json ./
RUN npm ci --ignore-scripts
COPY e2e-live/*.mjs e2e-live/*.ts e2e-live/tsconfig.json ./
RUN npm run check
ENTRYPOINT ["node", "/verify/verify.mjs"]
