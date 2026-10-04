<?php

if (!defined('ABSPATH')) {
    exit;
}

?>

<div class="hpc-domain-search" data-endpoint="<?php echo esc_url(rest_url('hpc/v1/domain-search')); ?>">

    <form class="hpc-domain-search-form" role="search" novalidate>

        <label for="hpc-domain-name" class="hpc-domain-search-label">
            <?php esc_html_e('Find your domain name', 'hosting-portal-connector'); ?>
        </label>

        <div class="hpc-domain-search-row">

            <input
                type="text"
                id="hpc-domain-name"
                class="hpc-domain-search-input"
                placeholder="example.com"
                autocomplete="off"
                autocapitalize="none"
                spellcheck="false"
                required
            >

            <button type="submit" class="hpc-domain-search-button">
                <?php esc_html_e('Search', 'hosting-portal-connector'); ?>
            </button>

        </div>

    </form>

    <div class="hpc-domain-search-result" role="status" aria-live="polite"></div>

</div>
