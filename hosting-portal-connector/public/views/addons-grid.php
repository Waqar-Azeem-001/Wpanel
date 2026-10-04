<?php

if (!defined('ABSPATH')) {
    exit;
}

?>

<div
    class="hpc-addons-grid hpc-columns-<?php echo esc_attr($columns); ?>"
>

    <?php foreach ($addons as $addon) : ?>

        <?php

        $name = isset($addon['name'])
            ? $addon['name']
            : '';

        $description = isset($addon['description'])
            ? $addon['description']
            : '';

        $pricing = isset($addon['pricing']) && is_array($addon['pricing'])
            ? $addon['pricing']
            : [];

        ?>

        <div class="hpc-addon-card">

            <h3 class="hpc-addon-title">
                <?php echo esc_html($name); ?>
            </h3>

            <?php if (!empty($description)) : ?>

                <p class="hpc-addon-description">
                    <?php echo esc_html($description); ?>
                </p>

            <?php endif; ?>

            <?php if (!empty($pricing)) : ?>

                <div class="hpc-addon-pricing">

                    <?php foreach ($pricing as $cycle => $price) : ?>

                        <div class="hpc-addon-price-row">

                            <span>
                                <?php
                                echo esc_html(
                                    HPC_Shortcodes::format_cycle_name($cycle)
                                );
                                ?>
                            </span>

                            <strong>
                                <?php
                                echo esc_html(
                                    ($price['currency'] ?? '') .
                                    ' ' .
                                    ($price['amount'] ?? '')
                                );
                                ?>
                            </strong>

                        </div>

                    <?php endforeach; ?>

                </div>

            <?php endif; ?>

        </div>

    <?php endforeach; ?>

</div>