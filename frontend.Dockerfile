# LcvSearch 前端 Dockerfile
# Vue3 + Vite + Nginx
FROM node:20-alpine AS builder

WORKDIR /app

# 安装依赖
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install

# 复制源码并构建
COPY frontend/ .
RUN npm run build

# 生产阶段：Nginx 托管静态文件
FROM nginx:alpine

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
