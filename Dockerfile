FROM node:22-bookworm-slim
WORKDIR /app
COPY --chown=node:node storage ./storage
ENV NODE_ENV=production NODE_OPTIONS=--max-old-space-size=128
USER node
CMD ["node","storage/main.mjs"]
