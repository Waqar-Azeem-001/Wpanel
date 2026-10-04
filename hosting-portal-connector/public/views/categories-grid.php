<?php

if (!defined('ABSPATH')) {
    exit;
}

?>

<div class="hpc-categories-grid">

    <?php foreach ($categories as $category) : ?>

        <?php

        $category_name = isset($category['name'])
            ? $category['name']
            : '';

        $category_slug = isset($category['slug'])
            ? $category['slug']
            : '';

        $product_count = isset($category['product_count'])
            ? absint($category['product_count'])
            : 0;

        ?>

        <div class="hpc-category-card">

            <h3 class="hpc-category-title">

                <?php
                echo esc_html(
                    $category_name
                );
                ?>

            </h3>


            <p class="hpc-category-count">

                <?php
                echo esc_html(
                    $product_count
                );
                ?>

                <?php

                echo $product_count === 1
                    ? ' plan'
                    : ' plans';

                ?>

            </p>


            <div class="hpc-category-shortcode">

                <code>

                    <?php
                    echo esc_html(
                        '[hosting_plans category="' .
                        $category_slug .
                        '"]'
                    );
                    ?>

                </code>

            </div>

        </div>

    <?php endforeach; ?>

</div>