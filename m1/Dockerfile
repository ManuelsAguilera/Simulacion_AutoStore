FROM node:22-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 8100
CMD ["npm", "start", "--", "--host", "0.0.0.0", "--poll", "1000"]
