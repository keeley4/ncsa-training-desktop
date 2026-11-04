#!/bin/bash
# Installs Moodle on first start, seeds synthetic courses, creates the read-only web service + token.
set -euo pipefail
cd /var/www/html
: "${MOODLE_WWWROOT:=http://moodle.localhost:8080}"
: "${MOODLE_ADMIN_PASSWORD:=Dev-only-Passw0rd!}"

cat > config.php <<PHP
<?php
unset(\$CFG); global \$CFG; \$CFG = new stdClass();
\$CFG->dbtype = 'mariadb'; \$CFG->dblibrary = 'native'; \$CFG->dbhost = 'db';
\$CFG->dbname = 'moodle'; \$CFG->dbuser = 'moodle'; \$CFG->dbpass = 'moodle'; \$CFG->prefix = 'mdl_';
\$CFG->dboptions = ['dbcollation' => 'utf8mb4_unicode_ci'];
\$CFG->wwwroot = '${MOODLE_WWWROOT}';
\$CFG->dataroot = '/var/moodledata'; \$CFG->admin = 'admin'; \$CFG->directorypermissions = 02777;
require_once(__DIR__ . '/lib/setup.php');
PHP
chown www-data:www-data config.php

echo "waiting for MariaDB..."
until mysqladmin --skip-ssl ping -h db -u moodle -pmoodle --silent; do sleep 2; done

chown -R www-data:www-data /var/moodledata
as_web() { runuser -u www-data -- "$@"; }
if ! mysql --skip-ssl -h db -u moodle -pmoodle moodle -e "SELECT 1 FROM mdl_config LIMIT 1" >/dev/null 2>&1; then
  echo "installing Moodle..."
  as_web php admin/cli/install_database.php --agree-license --lang=en \
    --adminuser=admin --adminpass="${MOODLE_ADMIN_PASSWORD}" --adminemail=admin@example.org \
    --fullname="Learn@NCSA (dev)" --shortname=dev --supportemail=admin@example.org
  as_web php /opt/dev/seed.php /opt/fixtures/moodle
fi
mkdir -p /shared && chmod 777 /shared
as_web php /opt/dev/ws_setup.php /shared/moodle_token
echo "Moodle ready at ${MOODLE_WWWROOT} (admin / see MOODLE_ADMIN_PASSWORD)"
exec apache2-foreground
