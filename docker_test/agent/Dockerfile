FROM node:22-slim
WORKDIR /t
RUN npm init -y >/dev/null 2>&1 && npm install openai >/dev/null 2>&1 && echo "openai installed"
COPY test_maxapi.mjs ./test_maxapi.mjs
ENV MAXAPI_BASE=http://host.docker.internal:8080/v1
ENV MAXAPI_KEY=sk-test
CMD ["node", "test_maxapi.mjs"]
