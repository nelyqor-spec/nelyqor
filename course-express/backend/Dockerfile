FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json tsconfig.json ./
RUN npm ci
COPY src ./src
COPY tests ./tests
RUN npx tsc --project tsconfig.json

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=8080
COPY --from=build /app/dist/src ./dist/src
COPY package.json ./
USER node
EXPOSE 8080
CMD ["node", "dist/src/server.js"]
