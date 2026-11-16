<?php
/**
 * The gateway on the Checkout block (WooCommerce Blocks). The block only shows the payment
 * option; paying happens on the order's pay page, as on the classic checkout.
 */

if (!defined('ABSPATH')) {
	exit;
}

use Automattic\WooCommerce\Blocks\Payments\Integrations\AbstractPaymentMethodType;

final class Agamsale_Razorpay_Blocks extends AbstractPaymentMethodType {
	protected $name = WC_Gateway_Agamsale_Razorpay::ID;

	public function initialize() {
		$this->settings = get_option('woocommerce_' . WC_Gateway_Agamsale_Razorpay::ID . '_settings', []);
	}

	public function is_active() {
		return ($this->settings['enabled'] ?? 'no') === 'yes' && Agamsale_Api::is_configured();
	}

	public function get_payment_method_script_handles() {
		wp_register_script(
			'agamsale-razorpay-blocks',
			plugins_url('assets/js/checkout-blocks.js', FIELD_SALES_PLUGIN_FILE),
			['wc-blocks-registry', 'wc-settings', 'wp-element', 'wp-html-entities'],
			FIELD_SALES_VERSION,
			true
		);
		return ['agamsale-razorpay-blocks'];
	}

	public function get_payment_method_data() {
		return [
			'title' => $this->settings['title'] ?? 'Razorpay',
			'description' => $this->settings['description'] ?? '',
			'supports' => ['products', 'refunds'],
		];
	}
}
