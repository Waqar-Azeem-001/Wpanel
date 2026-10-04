document.addEventListener('DOMContentLoaded', function () {

    const planCards = document.querySelectorAll('.hpc-plan-card');

    planCards.forEach(function (card) {

        const selector = card.querySelector('.hpc-cycle-select');

        if (!selector) {
            return;
        }

        function updatePlanPrice() {

            const selectedCycle = selector.value;

            const prices = card.querySelectorAll(
                '.hpc-price-value'
            );

            prices.forEach(function (price) {

                const priceCycle = price.getAttribute(
                    'data-cycle'
                );

                if (priceCycle === selectedCycle) {
                    price.classList.add('is-active');
                } else {
                    price.classList.remove('is-active');
                }

            });

            const orderButton = card.querySelector(
                '.hpc-order-button'
            );

            if (!orderButton) {
                return;
            }

            const baseUrl = orderButton.getAttribute(
                'data-base-url'
            );

            if (!baseUrl) {
                return;
            }

            try {

                const url = new URL(
                    baseUrl,
                    window.location.href
                );

                url.searchParams.set(
                    'billing_cycle',
                    selectedCycle
                );

                // The portal reads "cycle", and writes a custom period as custom:4.
                url.searchParams.set(
                    'cycle',
                    selectedCycle.replace('custom_', 'custom:')
                );

                orderButton.href = url.toString();

            } catch (error) {

                console.error(
                    'Hosting Portal Connector: Invalid order URL.',
                    error
                );
            }
        }

        selector.addEventListener(
            'change',
            updatePlanPrice
        );

        /*
         * Also initialize the card when page loads.
         */
        updatePlanPrice();

    });

});


/*
 * Domain search: asks this site (never the portal directly), shows the
 * answer, and links on to the portal to register the name.
 */
document.addEventListener('DOMContentLoaded', function () {

    document.querySelectorAll('.hpc-domain-search').forEach(function (box) {

        const form = box.querySelector('.hpc-domain-search-form');
        const input = box.querySelector('.hpc-domain-search-input');
        const button = box.querySelector('.hpc-domain-search-button');
        const result = box.querySelector('.hpc-domain-search-result');
        const endpoint = box.getAttribute('data-endpoint');

        if (!form || !input || !result || !endpoint) {
            return;
        }

        function show(text, kind, link) {

            result.textContent = '';
            result.className = 'hpc-domain-search-result' + (kind ? ' is-' + kind : '');

            const message = document.createElement('p');
            message.textContent = text;
            result.appendChild(message);

            if (link) {
                const a = document.createElement('a');
                a.className = 'hpc-domain-search-order';
                a.href = link.href;
                a.textContent = link.label;
                result.appendChild(a);
            }
        }

        form.addEventListener('submit', function (event) {

            event.preventDefault();

            const name = input.value.trim().toLowerCase();

            if (!name) {
                show('Enter a domain name such as example.com.', 'error');
                return;
            }

            button.disabled = true;
            show('Searching\u2026', '');

            fetch(endpoint + '?name=' + encodeURIComponent(name), {
                headers: { 'Accept': 'application/json' }
            })
                .then(function (response) {
                    return response.json();
                })
                .then(function (data) {

                    if (!data || !data.ok) {
                        show((data && data.message) || 'The search failed. Please try again.', 'error');
                        return;
                    }

                    if (data.available) {

                        show(
                            data.domain + ' is available' +
                            (data.price ? ' \u2014 ' + data.price + ' ' + data.currency + ' per year' : '') + '.',
                            'ok',
                            data.order_url ? { href: data.order_url, label: 'Register this domain' } : null
                        );

                    } else {

                        show(data.domain + ' is already taken. Try another name.', 'taken');
                    }
                })
                .catch(function () {
                    show('The search failed. Please try again.', 'error');
                })
                .finally(function () {
                    button.disabled = false;
                });
        });
    });
});
