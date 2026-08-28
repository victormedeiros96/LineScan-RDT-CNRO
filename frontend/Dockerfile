FROM node:22-alpine AS base

# ---- Dependencies ----
FROM base AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

# ---- Build ----
FROM deps AS build
COPY . .
RUN npm run build

# ---- Serve via nginx ----
FROM nginx:alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
