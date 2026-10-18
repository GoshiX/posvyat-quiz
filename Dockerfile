FROM node:24-bookworm-slim

# Change this default, or use: docker build --build-arg PORT=8080 ...
ARG PORT=3000
ENV NODE_ENV=production \
    PORT=${PORT} \
    HOST=0.0.0.0 \
    DATA_DIR=/app/data
WORKDIR /app
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
COPY --chown=node:node server ./server
COPY --chown=node:node public ./public
COPY --chown=node:node docs ./docs
RUN mkdir -p /app/data && chown node:node /app/data
USER node
VOLUME ["/app/data"]
EXPOSE ${PORT}
HEALTHCHECK --interval=20s --timeout=5s --start-period=5s --retries=3 CMD node -e "fetch('http://127.0.0.1:'+process.env.PORT+'/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server/index.mjs"]
