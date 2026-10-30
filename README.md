# Field Sales WooCommerce Connector

WooCommerce connector app for Field Sales.

Enables Field Sales to sync products, stock, customers, and orders with a WooCommerce store via the WooCommerce REST API v3 and OAuth 1.0a authentication.

## Installation

```bash
bench get-app field_sales_woocommerce
bench --site [your-site] install-app field_sales_woocommerce
```

## Features

- **Products**: Synchronize simple and variable products, regular and sale prices, and stock tracking.
- **Stock**: Real-time stock status and single-pool pseudo location ("woo").
- **Customers**: Create and update WooCommerce customers from Retailer records.
- **Orders**: Push submitted Field Orders as either Draft (`pending`) or Confirmed (`processing`) WooCommerce orders.
- **Webhooks**: Signature verification (`X-WC-Webhook-Signature`) and topic translation for product, stock, and order events.
- **Embedded Admin**: WordPress plugin to embed Field Sales inside `wp-admin`.

## License

MIT
