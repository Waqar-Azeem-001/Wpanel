"""
The recommended plugins and themes a customer can install in one click from the WordPress overview. Only these names are accepted (the one-file tool installs by name from
wordpress.org with WordPress's own installer, and also checks the name's shape).
"""

PLUGINS = [
    {"slug": "wordpress-seo", "label": "Yoast SEO", "about": "Search engine optimisation: titles, descriptions and a sitemap."},
    {"slug": "wordfence", "label": "Wordfence Security", "about": "Firewall and malware scanner for WordPress."},
    {"slug": "updraftplus", "label": "UpdraftPlus", "about": "Backups of the website, with restore from the dashboard."},
    {"slug": "wp-super-cache", "label": "WP Super Cache", "about": "Static page cache that makes pages load faster."},
    {"slug": "contact-form-7", "label": "Contact Form 7", "about": "Contact forms for your visitors."},
    {"slug": "woocommerce", "label": "WooCommerce", "about": "Sell products and services online."},
    {"slug": "elementor", "label": "Elementor", "about": "Page builder with a drag and drop editor."},
    {"slug": "really-simple-ssl", "label": "Really Simple SSL", "about": "Fixes mixed-content problems after switching to HTTPS."},
]

THEMES = [
    {"slug": "astra", "label": "Astra", "about": "Fast, flexible theme for blogs, shops and business sites."},
    {"slug": "kadence", "label": "Kadence", "about": "Lightweight theme with a clean block editor."},
    {"slug": "twentytwentyfive", "label": "Twenty Twenty-Five", "about": "WordPress's own default theme."},
]


def kind_of(slug):
    """"plugin", "theme", or "" for a name that is not recommended."""
    if any(p["slug"] == slug for p in PLUGINS):
        return "plugin"
    if any(t["slug"] == slug for t in THEMES):
        return "theme"
    return ""
