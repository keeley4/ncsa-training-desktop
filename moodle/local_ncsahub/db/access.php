<?php
defined('MOODLE_INTERNAL') || die();

$capabilities = [
    'local/ncsahub:readoutbox' => [
        'riskbitmask' => RISK_PERSONAL,
        'captype' => 'read',
        'contextlevel' => CONTEXT_SYSTEM,
        'archetypes' => [],
    ],
];
