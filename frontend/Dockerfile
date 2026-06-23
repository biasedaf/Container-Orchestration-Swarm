FROM nginx:alpine

# Remove default config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom Nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy dashboard HTML
COPY dashboard.html /usr/share/nginx/html/dashboard.html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
