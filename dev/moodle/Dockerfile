# Dev Moodle that mirrors production (web.illinois.edu): Moodle 4.5 LTS, PHP 8.3, MariaDB 10.11,
# exec/shell_exec/system/passthru disabled, 60 s max execution time. Synthetic data only.
FROM php:8.3-apache

RUN apt-get update \
 && apt-get install -y --no-install-recommends libicu-dev libzip-dev libpng-dev libjpeg62-turbo-dev \
      libfreetype6-dev libxml2-dev curl mariadb-client \
 && docker-php-ext-configure gd --with-freetype --with-jpeg \
 && docker-php-ext-install -j"$(nproc)" intl zip gd mysqli opcache exif soap \
 && rm -rf /var/lib/apt/lists/*

ARG MOODLE_VERSION=4.5.15
RUN curl -fsSL "https://download.moodle.org/download.php/direct/stable405/moodle-${MOODLE_VERSION}.tgz" | tar xz -C /var/www \
 && rm -rf /var/www/html && mv /var/www/moodle /var/www/html \
 && mkdir -p /var/moodledata && chown -R www-data:www-data /var/moodledata /var/www/html

COPY dev/moodle/php.ini /usr/local/etc/php/conf.d/zz-moodle.ini
RUN sed -i 's/Listen 80$/Listen 8080/' /etc/apache2/ports.conf \
 && sed -i 's/<VirtualHost \*:80>/<VirtualHost *:8080>/' /etc/apache2/sites-available/000-default.conf

COPY dev/moodle/entrypoint.sh /opt/dev/entrypoint.sh
COPY dev/moodle/seed.php dev/moodle/ws_setup.php /opt/dev/
COPY fixtures/moodle /opt/fixtures/moodle
EXPOSE 8080
ENTRYPOINT ["bash", "/opt/dev/entrypoint.sh"]
