FROM node:20-alpine

RUN apk add --no-cache openssl

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY prisma ./prisma
RUN npx prisma generate

COPY . .
RUN mkdir -p uploads

EXPOSE 4000

CMD ["sh", "-c", "npx prisma db push && node src/index.js"]
