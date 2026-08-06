FROM node:22-slim
WORKDIR /t
RUN npm init -y >/dev/null 2>&1 && npm install @anthropic-ai/sdk >/dev/null 2>&1 && echo "anthropic sdk installed"
COPY test_anth.mjs ./test_anth.mjs
ENV MAXAPI_BASE=http://host.docker.internal:8080
ENV MAXAPI_KEY=sk-test
CMD ["node", "test_anth.mjs"]
