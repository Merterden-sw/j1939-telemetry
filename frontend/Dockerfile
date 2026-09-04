# ---------------------------------------------------------------------------
# Web arayuzu: nginx uzerinde statik dosyalar + backend proxy
# Derleme adimi gerektirmeyen bagimliliksiz (vanilla) bir arayuzdur.
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/telemetry.conf

COPY index.html /usr/share/nginx/html/index.html
COPY css/       /usr/share/nginx/html/css/
COPY js/        /usr/share/nginx/html/js/
# Istege bagli arac gorselleri (bos olabilir; arayuz SVG cizimine doner)
COPY img/       /usr/share/nginx/html/img/

EXPOSE 80

HEALTHCHECK --interval=15s --timeout=4s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
