<?php

if (!defined('ABSPATH')) {
    exit;
}

?>

<div
    class="hpc-plans-grid hpc-columns-<?php echo esc_attr($columns); ?>"
>

    <?php foreach ($products as $product) : ?>

        <?php

        include HPC_PLUGIN_DIR . 'public/views/plan-card.php';

        ?>

    <?php endforeach; ?>

</div>