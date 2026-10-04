<?php

if (!defined('ABSPATH')) {
    exit;
}

?>

<div class="hpc-domain-pricing">

    <div class="hpc-domain-table-wrap">

        <table class="hpc-domain-table">

            <thead>

                <tr>
                    <th>Domain</th>
                    <th>Register</th>
                    <th>Renew</th>
                    <th>Transfer</th>
                </tr>

            </thead>

            <tbody>

                <?php foreach ($domains as $domain) : ?>

                    <?php

                    $tld = isset($domain['tld'])
                        ? $domain['tld']
                        : '';

                    $register = isset($domain['register'])
                        ? $domain['register']
                        : [];

                    $renew = isset($domain['renew'])
                        ? $domain['renew']
                        : [];

                    $transfer = isset($domain['transfer'])
                        ? $domain['transfer']
                        : [];

                    ?>

                    <tr>

                        <td class="hpc-domain-tld">

                            <?php
                            echo esc_html($tld);
                            ?>

                        </td>


                        <td>

                            <?php

                            echo esc_html(
                                ($register['currency'] ?? '') .
                                ' ' .
                                ($register['amount'] ?? '')
                            );

                            ?>

                        </td>


                        <td>

                            <?php

                            echo esc_html(
                                ($renew['currency'] ?? '') .
                                ' ' .
                                ($renew['amount'] ?? '')
                            );

                            ?>

                        </td>


                        <td>

                            <?php

                            echo esc_html(
                                ($transfer['currency'] ?? '') .
                                ' ' .
                                ($transfer['amount'] ?? '')
                            );

                            ?>

                        </td>

                    </tr>

                <?php endforeach; ?>

            </tbody>

        </table>

    </div>

</div>