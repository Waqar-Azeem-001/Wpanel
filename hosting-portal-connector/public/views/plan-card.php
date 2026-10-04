<?php

if (!defined('ABSPATH')) {
    exit;
}

$product_id = isset($product['id'])
    ? absint($product['id'])
    : 0;

$product_name = isset($product['name'])
    ? $product['name']
    : '';

$product_description = isset($product['description'])
    ? $product['description']
    : '';

$pricing = isset($product['pricing']) && is_array($product['pricing'])
    ? $product['pricing']
    : [];

$features = isset($product['features']) && is_array($product['features'])
    ? $product['features']
    : [];

$order_url = isset($product['order_url'])
    ? $product['order_url']
    : '';

$featured = !empty($product['featured']);

$default_cycle = '';

if (!empty($pricing)) {
    $pricing_keys = array_keys($pricing);
    $default_cycle = reset($pricing_keys);
}

?>

<div
    class="hpc-plan-card <?php echo $featured ? 'hpc-plan-card-featured' : ''; ?>"
    data-product-id="<?php echo esc_attr($product_id); ?>"
>

    <?php if ($featured) : ?>

        <div class="hpc-plan-badge">
            Most Popular
        </div>

    <?php endif; ?>

    <div class="hpc-plan-header">

        <h3 class="hpc-plan-title">
            <?php echo esc_html($product_name); ?>
        </h3>

        <?php if (!empty($product_description)) : ?>

            <p class="hpc-plan-description">
                <?php echo esc_html($product_description); ?>
            </p>

        <?php endif; ?>

    </div>

    <?php if (!empty($pricing)) : ?>

        <div class="hpc-plan-pricing">

            <div class="hpc-price-display">

                <?php foreach ($pricing as $cycle => $price) : ?>

                    <?php

                    $amount = isset($price['amount'])
                        ? $price['amount']
                        : '';

                    $currency = isset($price['currency'])
                        ? $price['currency']
                        : '';

                    ?>

                    <div
                        class="hpc-price-value <?php echo $cycle === $default_cycle ? 'is-active' : ''; ?>"
                        data-cycle="<?php echo esc_attr($cycle); ?>"
                    >

                        <span class="hpc-price-currency">
                            <?php echo esc_html($currency); ?>
                        </span>

                        <span class="hpc-price-amount">
                            <?php echo esc_html($amount); ?>
                        </span>

                        <span class="hpc-price-period">
                            /
                            <?php
                            echo esc_html(
                                HPC_Shortcodes::format_cycle_label($cycle)
                            );
                            ?>
                        </span>

                    </div>

                <?php endforeach; ?>

            </div>

            <?php if (count($pricing) > 1) : ?>

                <div class="hpc-cycle-selector">

                    <label
                        for="hpc-cycle-<?php echo esc_attr($product_id); ?>"
                        class="hpc-cycle-label"
                    >
                        Billing Cycle
                    </label>

                    <select
                        id="hpc-cycle-<?php echo esc_attr($product_id); ?>"
                        class="hpc-cycle-select"
                    >

                        <?php foreach ($pricing as $cycle => $price) : ?>

                            <option
                                value="<?php echo esc_attr($cycle); ?>"
                                <?php selected($cycle, $default_cycle); ?>
                            >
                                <?php
                                echo esc_html(
                                    HPC_Shortcodes::format_cycle_name($cycle)
                                );
                                ?>
                            </option>

                        <?php endforeach; ?>

                    </select>

                </div>

            <?php endif; ?>

        </div>

    <?php endif; ?>

    <?php if (!empty($features)) : ?>

        <div class="hpc-features-wrapper">

            <ul class="hpc-plan-features">

                <?php foreach ($features as $feature) : ?>

                    <li>
                        <span
                            class="hpc-feature-check"
                            aria-hidden="true"
                        >
                            ✓
                        </span>

                        <span>
                            <?php echo esc_html($feature); ?>
                        </span>
                    </li>

                <?php endforeach; ?>

            </ul>

        </div>

    <?php endif; ?>

    <?php if (!empty($order_url)) : ?>

        <div class="hpc-plan-action">

            <a
                href="<?php echo esc_url($order_url); ?>"
                class="hpc-order-button"
                data-base-url="<?php echo esc_url($order_url); ?>"
            >
                Get Started
            </a>

        </div>

    <?php endif; ?>

</div>