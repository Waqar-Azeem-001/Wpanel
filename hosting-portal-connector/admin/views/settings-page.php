<?php

if (!defined('ABSPATH')) {
    exit;
}

/*
|--------------------------------------------------------------------------
| Plugin Settings
|--------------------------------------------------------------------------
*/

$portal_url = get_option('hpc_portal_url', '');
$api_key = get_option('hpc_api_key', '');
$webhook_secret = get_option('hpc_webhook_secret', '');
$last_request = get_option('hpc_last_request', []);
$last_error = get_option('hpc_last_error', '');
$last_webhook = get_option('hpc_last_webhook', []);
$connection_mode = get_option('hpc_connection_mode', 'mock');
$cache_duration = absint(get_option('hpc_cache_duration', 900));

/*
|--------------------------------------------------------------------------
| Connection Information
|--------------------------------------------------------------------------
*/

$last_status = get_option('hpc_last_connection_status', '');
$last_message = get_option('hpc_last_connection_message', '');
$last_connection = get_option('hpc_last_connection_time', '');
$last_success = get_option('hpc_last_successful_connection', '');
$last_sync = get_option('hpc_last_sync_time', '');
$product_count = absint(get_option('hpc_last_product_count', 0));

?>

<div class="wrap">

    <h1>Hosting Portal Connector</h1>

    <p>
        Connect this WordPress website with your Hosting Management Portal.
    </p>


    <?php
    /*
    |--------------------------------------------------------------------------
    | Cache Cleared Notice
    |--------------------------------------------------------------------------
    */

    if (isset($_GET['hpc_cache_cleared'])) :
    ?>

        <div class="notice notice-success is-dismissible">
            <p>Hosting Portal cache cleared successfully.</p>
        </div>

    <?php
    endif;
    ?>


    <?php
    /*
    |--------------------------------------------------------------------------
    | Product Refresh Notice
    |--------------------------------------------------------------------------
    */

    if (isset($_GET['hpc_refresh'])) :

        $refresh_status = sanitize_text_field(
            wp_unslash($_GET['hpc_refresh'])
        );

        if ($refresh_status === 'success') :
        ?>

            <div class="notice notice-success is-dismissible">
                <p>Products refreshed successfully.</p>
            </div>

        <?php
        elseif ($refresh_status === 'failed') :
        ?>

            <div class="notice notice-error is-dismissible">
                <p>
                    Unable to refresh products. Check the connection details below.
                </p>
            </div>

        <?php
        endif;

    endif;
    ?>


    <?php
    /*
    |--------------------------------------------------------------------------
    | Connection Test Result
    |--------------------------------------------------------------------------
    */

    if (isset($_GET['hpc_test'])) :

        $test_result = get_transient('hpc_api_test_result');

        delete_transient('hpc_api_test_result');

        if (!empty($test_result)) :

            if (!empty($test_result['success'])) :
            ?>

                <div class="notice notice-success is-dismissible">

                    <p>
                        <strong>Connection Test Successful</strong>
                    </p>

                    <?php if (!empty($test_result['source'])) : ?>

                        <p>
                            Source:
                            <strong>
                                <?php
                                echo esc_html(
                                    ucfirst($test_result['source'])
                                );
                                ?>
                            </strong>
                        </p>

                    <?php endif; ?>


                    <?php
                    if (
                        isset($test_result['data']['products']) &&
                        is_array($test_result['data']['products'])
                    ) :
                    ?>

                        <p>
                            <?php
                            echo esc_html(
                                count($test_result['data']['products'])
                            );
                            ?>
                            hosting plans received.
                        </p>

                    <?php endif; ?>

                </div>

            <?php
            else :

                $error_message = 'Unknown error.';

                if (!empty($test_result['message'])) {
                    $error_message = $test_result['message'];
                }
            ?>

                <div class="notice notice-error is-dismissible">

                    <p>
                        <strong>Connection Test Failed</strong>
                    </p>

                    <p>
                        <?php echo esc_html($error_message); ?>
                    </p>

                </div>

            <?php
            endif;

        endif;

    endif;
    ?>


    <!-- ============================================================ -->
    <!-- Integration Status -->
    <!-- ============================================================ -->

    <h2>Integration Status</h2>

    <div class="hpc-admin-status-grid">

        <div class="hpc-admin-status-card">

            <span class="hpc-status-label">
                Mode
            </span>

            <strong class="hpc-status-value">

                <?php
                if ($connection_mode === 'live') {
                    echo 'Live API';
                } else {
                    echo 'Mock / Development';
                }
                ?>

            </strong>

        </div>


        <div class="hpc-admin-status-card">

            <span class="hpc-status-label">
                Connection
            </span>

            <strong
                class="hpc-status-value <?php echo esc_attr('hpc-status-' . $last_status); ?>"
            >

                <?php

                if ($last_status === 'success') {
                    echo 'Connected';
                } elseif ($last_status === 'failed') {
                    echo 'Failed';
                } else {
                    echo 'Not Tested';
                }

                ?>

            </strong>

        </div>


        <div class="hpc-admin-status-card">

            <span class="hpc-status-label">
                Products
            </span>

            <strong class="hpc-status-value">
                <?php echo esc_html($product_count); ?>
            </strong>

        </div>


        <div class="hpc-admin-status-card">

            <span class="hpc-status-label">
                Last Sync
            </span>

            <strong class="hpc-status-value">

                <?php

                if (!empty($last_sync)) {
                    echo esc_html($last_sync);
                } else {
                    echo 'Never';
                }

                ?>

            </strong>

        </div>

    </div>


    <!-- ============================================================ -->
    <!-- Connection Details -->
    <!-- ============================================================ -->

    <div class="hpc-connection-details">

        <h2>Connection Details</h2>

        <table class="widefat striped">

            <tbody>

                <tr>

                    <th>
                        Portal URL
                    </th>

                    <td>

                        <?php

                        if (!empty($portal_url)) {
                            echo esc_html($portal_url);
                        } else {
                            echo 'Not configured';
                        }

                        ?>

                    </td>

                </tr>


                <tr>

                    <th>
                        Last Connection Test
                    </th>

                    <td>

                        <?php

                        if (!empty($last_connection)) {
                            echo esc_html($last_connection);
                        } else {
                            echo 'Never';
                        }

                        ?>

                    </td>

                </tr>


                <tr>

                    <th>
                        Last Successful Connection
                    </th>

                    <td>

                        <?php

                        if (!empty($last_success)) {
                            echo esc_html($last_success);
                        } else {
                            echo 'Never';
                        }

                        ?>

                    </td>

                </tr>


                <tr>

                    <th>
                        Last Message
                    </th>

                    <td>

                        <?php

                        if (!empty($last_message)) {
                            echo esc_html($last_message);
                        } else {
                            echo 'No connection test performed yet.';
                        }

                        ?>

                    </td>

                </tr>

            </tbody>

        </table>

    </div>


    <hr>


    <!-- ============================================================ -->
    <!-- Connection Settings -->
    <!-- ============================================================ -->

    <!-- ============================================================ -->
    <!-- Diagnostics -->
    <!-- ============================================================ -->

    <div class="hpc-connection-details">

        <h2>Diagnostics</h2>

        <table class="widefat striped">

            <tbody>

                <tr>
                    <th>Last portal request</th>
                    <td>
                        <?php if (!empty($last_request['endpoint'])) : ?>
                            <?php echo esc_html($last_request['endpoint']); ?>
                            &mdash; <?php echo esc_html((int) $last_request['ms']); ?> ms
                            &mdash; <?php echo !empty($last_request['ok']) ? 'OK' : 'Failed'; ?>
                            (<?php echo esc_html($last_request['time']); ?>)
                        <?php else : ?>
                            No request made yet.
                        <?php endif; ?>
                    </td>
                </tr>

                <tr>
                    <th>Last error</th>
                    <td><?php echo $last_error !== '' ? esc_html($last_error) : 'None'; ?></td>
                </tr>

                <tr>
                    <th>Last webhook</th>
                    <td>
                        <?php if (!empty($last_webhook['time'])) : ?>
                            <?php echo !empty($last_webhook['ok']) ? 'Accepted' : 'Rejected (bad signature)'; ?>
                            (<?php echo esc_html($last_webhook['time']); ?>)
                        <?php else : ?>
                            None received.
                        <?php endif; ?>
                    </td>
                </tr>

            </tbody>

        </table>

    </div>


    <hr>


    <h2>Connection Settings</h2>

    <form
        method="post"
        action="options.php"
    >

        <?php settings_fields('hpc_settings_group'); ?>

        <table class="form-table">

            <tbody>

                <tr>

                    <th scope="row">

                        <label for="hpc_connection_mode">
                            Connection Mode
                        </label>

                    </th>

                    <td>

                        <select
                            name="hpc_connection_mode"
                            id="hpc_connection_mode"
                        >

                            <option
                                value="mock"
                                <?php selected($connection_mode, 'mock'); ?>
                            >
                                Mock / Development
                            </option>

                            <option
                                value="live"
                                <?php selected($connection_mode, 'live'); ?>
                            >
                                Live Django API
                            </option>

                        </select>

                        <p class="description">
                            Use Mock mode while the Django Storefront API is under development.
                        </p>

                    </td>

                </tr>


                <tr>

                    <th scope="row">

                        <label for="hpc_portal_url">
                            Portal URL
                        </label>

                    </th>

                    <td>

                        <input
                            type="url"
                            name="hpc_portal_url"
                            id="hpc_portal_url"
                            value="<?php echo esc_attr($portal_url); ?>"
                            class="regular-text"
                            placeholder="https://portal.example.com"
                        >

                        <p class="description">
                            Base URL of the Django Hosting Management Portal.
                        </p>

                    </td>

                </tr>


                <tr>

                    <th scope="row">

                        <label for="hpc_api_key">
                            API Credential
                        </label>

                    </th>

                    <td>

                        <input
                            type="password"
                            name="hpc_api_key"
                            id="hpc_api_key"
                            value="<?php echo esc_attr($api_key); ?>"
                            class="regular-text"
                            autocomplete="off"
                        >

                        <p class="description">
                            Written as <code>public_id:secret</code>, from the storefront's page in the portal
                            (Setup &rarr; External storefronts). The secret is shown there only once.
                        </p>

                    </td>

                </tr>


                <tr>

                    <th scope="row">

                        <label for="hpc_webhook_secret">
                            Webhook Signing Secret
                        </label>

                    </th>

                    <td>

                        <input
                            type="password"
                            name="hpc_webhook_secret"
                            id="hpc_webhook_secret"
                            value="<?php echo esc_attr($webhook_secret); ?>"
                            class="regular-text"
                            autocomplete="off"
                        >

                        <p class="description">
                            Optional. Generated on the storefront's page in the portal. With it, the portal clears
                            this site's cache the moment a plan changes. Give the portal this webhook address:
                            <code><?php echo esc_html(rest_url('hpc/v1/webhook')); ?></code>
                        </p>

                    </td>

                </tr>


                <tr>

                    <th scope="row">

                        <label for="hpc_cache_duration">
                            Cache Duration
                        </label>

                    </th>

                    <td>

                        <input
                            type="number"
                            name="hpc_cache_duration"
                            id="hpc_cache_duration"
                            value="<?php echo esc_attr($cache_duration); ?>"
                            min="60"
                            step="60"
                        >

                        <span>
                            seconds
                        </span>

                        <p class="description">
                            Recommended: 900 seconds (15 minutes).
                        </p>

                    </td>

                </tr>

            </tbody>

        </table>

        <?php submit_button('Save Settings'); ?>

    </form>


    <hr>


    <!-- ============================================================ -->
    <!-- Connection Test -->
    <!-- ============================================================ -->

    <h2>Connection Test</h2>

    <p>
        Test whether the plugin can retrieve hosting products from the configured source.
    </p>


    <form
        method="post"
        action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
        style="display:inline-block;"
    >

        <input
            type="hidden"
            name="action"
            value="hpc_test_products"
        >

        <?php wp_nonce_field('hpc_test_products'); ?>

        <?php
        submit_button(
            'Test Connection',
            'secondary',
            'submit',
            false
        );
        ?>

    </form>


    <!-- ============================================================ -->
    <!-- Refresh Products -->
    <!-- ============================================================ -->

    <form
        method="post"
        action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
        style="display:inline-block; margin-left:8px;"
    >

        <input
            type="hidden"
            name="action"
            value="hpc_refresh_products"
        >

        <?php wp_nonce_field('hpc_refresh_products'); ?>

        <?php
        submit_button(
            'Refresh Products',
            'secondary',
            'submit',
            false
        );
        ?>

    </form>


    <hr>


    <!-- ============================================================ -->
    <!-- Cache -->
    <!-- ============================================================ -->

    <h2>Cache</h2>

    <p>
        Cached hosting plans are used to reduce requests to the Hosting Portal.
    </p>

    <p>

        Current cache duration:

        <strong>
            <?php echo esc_html($cache_duration); ?>
            seconds
        </strong>

    </p>


    <form
        method="post"
        action="<?php echo esc_url(admin_url('admin-post.php')); ?>"
    >

        <input
            type="hidden"
            name="action"
            value="hpc_clear_cache"
        >

        <?php wp_nonce_field('hpc_clear_cache'); ?>

        <?php
        submit_button(
            'Clear Cache',
            'secondary',
            'submit',
            false
        );
        ?>

    </form>

</div>