<?php
/**
 * "Razorpay" payment method on the WooCommerce checkout, run through Agamsale.
 *
 * Flow:
 * 1. process_payment(): the order stays Pending payment; the customer goes to the order's
 *    pay page (receipt_page), on the classic and the Blocks checkout alike.
 * 2. receipt_page(): Agamsale creates (or reuses) the Razorpay order for the WooCommerce order;
 *    its amount and currency come from WooCommerce on Agamsale's side, not from this page. The
 *    page opens Razorpay Checkout (checkout.razorpay.com). Card, UPI and bank details go only to
 *    Razorpay; this plugin never sees or stores them.
 * 3. Razorpay hands the page razorpay_payment_id / razorpay_order_id / razorpay_signature, which
 *    it posts to callback() (WC-API agamsale_razorpay) with the order key.
 * 4. callback(): Agamsale checks the signature and the payment itself (order, amount, currency,
 *    captured); only then is the order marked paid here (payment_complete()).
 *
 * If the customer closes the browser after paying, Razorpay's webhook to Agamsale marks the
 * order paid through the WooCommerce REST API instead. Refunds made on the order go to
 * Razorpay through Agamsale (process_refund()). Uses WC_Order methods only, so it works with
 * HPOS (custom order tables).
 */

if (!defined('ABSPATH')) {
	exit;
}

class WC_Gateway_Agamsale_Razorpay extends WC_Payment_Gateway {
	const ID = 'agamsale_razorpay';
	const META_RAZORPAY_ORDER = '_agamsale_razorpay_order_id';

	public function __construct() {
		$this->id = self::ID;
		$this->method_title = 'Razorpay (Agamsale)';
		$this->method_description = 'Customers pay with UPI, cards, netbanking or wallets on Razorpay Checkout. Payments are created and verified by Agamsale, which holds the Razorpay keys (Agamsale → Settings → Payments).';
		$this->has_fields = false;
		$this->supports = ['products', 'refunds'];
		$this->icon = '';

		$this->init_form_fields();
		$this->init_settings();
		$this->title = $this->get_option('title', 'Razorpay');
		$this->description = $this->get_option('description');
		$this->enabled = $this->get_option('enabled', 'no');

		add_action('woocommerce_update_options_payment_gateways_' . $this->id, [$this, 'process_admin_options']);
		add_action('woocommerce_receipt_' . $this->id, [$this, 'receipt_page']);
		add_action('woocommerce_api_' . $this->id, [$this, 'callback']);
	}

	public function init_form_fields() {
		$this->form_fields = [
			'enabled' => [
				'title' => 'Enable/Disable',
				'type' => 'checkbox',
				'label' => 'Enable Razorpay through Agamsale',
				'default' => 'no',
			],
			'title' => [
				'title' => 'Title',
				'type' => 'text',
				'description' => 'What customers see at checkout.',
				'default' => 'Razorpay (UPI, cards, netbanking)',
				'desc_tip' => true,
			],
			'description' => [
				'title' => 'Description',
				'type' => 'textarea',
				'default' => 'Pay securely with UPI, cards, netbanking or wallets. You will finish the payment on Razorpay.',
			],
			'auto_open' => [
				'title' => 'Open Razorpay automatically',
				'type' => 'checkbox',
				'label' => 'Open the Razorpay window as soon as the payment page loads',
				'default' => 'yes',
			],
		];
	}

	/** Shown at checkout only when Agamsale is connected. */
	public function is_available() {
		return parent::is_available() && Agamsale_Api::is_configured();
	}

	public function process_payment($order_id) {
		$order = wc_get_order($order_id);
		if (!$order) {
			wc_add_notice('The order could not be found. Please try again.', 'error');
			return ['result' => 'failure'];
		}
		// Stays Pending payment until Agamsale verifies the payment.
		return [
			'result' => 'success',
			'redirect' => $order->get_checkout_payment_url(true),
		];
	}

	/** The pay page: Razorpay Checkout for this order. */
	public function receipt_page($order_id) {
		$order = wc_get_order($order_id);
		if (!$order) {
			return;
		}
		if ($order->is_paid()) {
			echo '<p>' . esc_html('This order is already paid.') . '</p>';
			return;
		}

		$answer = Agamsale_Api::call('create_checkout_order', ['store_order_id' => (string) $order->get_id()]);
		if (is_wp_error($answer)) {
			$order->add_order_note('Razorpay payment could not start: ' . $answer->get_error_message());
			echo '<div class="woocommerce-error" role="alert">' . esc_html($this->customer_message($answer)) . '</div>';
			echo '<p><a class="button" href="' . esc_url($order->get_checkout_payment_url(true)) . '">' . esc_html('Try again') . '</a></p>';
			return;
		}

		$order->update_meta_data(self::META_RAZORPAY_ORDER, sanitize_text_field($answer['razorpay_order_id']));
		$order->save();

		wp_enqueue_script('razorpay-checkout', 'https://checkout.razorpay.com/v1/checkout.js', [], null, true);
		wp_enqueue_script('agamsale-razorpay-pay', plugins_url('assets/js/pay.js', FIELD_SALES_PLUGIN_FILE), ['razorpay-checkout'], FIELD_SALES_VERSION, true);
		wp_localize_script('agamsale-razorpay-pay', 'agamsaleRazorpay', [
			'key' => $answer['key_id'],
			'orderId' => $answer['razorpay_order_id'],
			'amount' => (int) $answer['amount'],
			'currency' => $answer['currency'],
			'name' => get_bloginfo('name'),
			'description' => sprintf('Order #%s', $order->get_order_number()),
			'prefill' => $answer['prefill'] ?? [],
			'autoOpen' => $this->get_option('auto_open', 'yes') === 'yes',
			'messages' => [
				'cancelled' => 'Payment cancelled. Your order is saved: pay now to complete it, or come back later from your account.',
				'failed' => 'The payment did not go through: ',
				'processing' => 'Confirming your payment…',
			],
		]);
		?>
		<div id="agamsale-razorpay">
			<p><?php echo esc_html('Pay for order #' . $order->get_order_number() . ' with Razorpay.'); ?></p>
			<div id="agamsale-razorpay-message" role="status" aria-live="polite"></div>
			<form id="agamsale-razorpay-form" method="post" action="<?php echo esc_url(WC()->api_request_url(self::ID)); ?>">
				<input type="hidden" name="order_id" value="<?php echo esc_attr($order->get_id()); ?>" />
				<input type="hidden" name="order_key" value="<?php echo esc_attr($order->get_order_key()); ?>" />
				<input type="hidden" name="razorpay_payment_id" value="" />
				<input type="hidden" name="razorpay_order_id" value="" />
				<input type="hidden" name="razorpay_signature" value="" />
				<button type="button" class="button alt" id="agamsale-razorpay-pay"><?php echo esc_html('Pay now'); ?></button>
				<a class="button cancel" href="<?php echo esc_url($order->get_cancel_order_url()); ?>"><?php echo esc_html('Cancel order'); ?></a>
			</form>
		</div>
		<?php
	}

	/** WC-API agamsale_razorpay: the pay page posts Razorpay's answer here. */
	public function callback() {
		$order_id = isset($_POST['order_id']) ? absint(wp_unslash($_POST['order_id'])) : 0;
		$order_key = isset($_POST['order_key']) ? sanitize_text_field(wp_unslash($_POST['order_key'])) : '';
		$order = $order_id ? wc_get_order($order_id) : false;
		// The order key proves the post comes from this order's pay page (as for guest orders).
		if (!$order || !$order_key || !hash_equals($order->get_order_key(), $order_key) || $order->get_payment_method() !== self::ID) {
			wp_safe_redirect(wc_get_page_permalink('cart'));
			exit;
		}

		$fields = [];
		foreach (['razorpay_payment_id', 'razorpay_order_id', 'razorpay_signature'] as $field) {
			$value = isset($_POST[$field]) ? sanitize_text_field(wp_unslash($_POST[$field])) : '';
			if (!preg_match('/^[A-Za-z0-9_]{1,128}$/', $value)) {
				$value = '';
			}
			$fields[$field] = $value;
		}
		if (in_array('', $fields, true)) {
			wc_add_notice('The payment was not completed. Please try again.', 'error');
			wp_safe_redirect($order->get_checkout_payment_url(true));
			exit;
		}

		if ($order->is_paid()) {
			// Already confirmed (Agamsale's webhook was faster, or the page was posted twice).
			wp_safe_redirect($this->get_return_url($order));
			exit;
		}

		$answer = Agamsale_Api::call('verify_checkout_payment', ['store_order_id' => (string) $order->get_id()] + $fields);
		if (is_wp_error($answer)) {
			$code = $answer->get_error_code();
			$order->add_order_note(sprintf('Razorpay payment %s not confirmed: %s', $fields['razorpay_payment_id'], $answer->get_error_message()));
			if ($code === 'NOT_PAID') {
				$order->update_status('failed', 'Razorpay payment failed.');
			}
			wc_add_notice($this->customer_message($answer), 'error');
			wp_safe_redirect($order->get_checkout_payment_url(true));
			exit;
		}

		$order->update_meta_data(self::META_RAZORPAY_ORDER, $fields['razorpay_order_id']);
		$order->payment_complete($fields['razorpay_payment_id']);
		$order->add_order_note(sprintf('Razorpay payment %s verified by Agamsale.', $fields['razorpay_payment_id']));
		if (function_exists('WC') && WC()->cart) {
			WC()->cart->empty_cart();
		}
		wp_safe_redirect($this->get_return_url($order));
		exit;
	}

	/** Refund on the order (wp-admin) → Razorpay, through Agamsale. */
	public function process_refund($order_id, $amount = null, $reason = '') {
		$order = wc_get_order($order_id);
		if (!$order) {
			return new WP_Error('agamsale_refund', 'Order not found.');
		}
		if ($amount === null || (float) $amount <= 0) {
			return new WP_Error('agamsale_refund', 'Enter a refund amount.');
		}
		// WooCommerce saves the refund before asking the gateway; its id makes the request
		// unique, so the same refund is never sent to Razorpay twice.
		$refund_id = 0;
		foreach ($order->get_refunds() as $refund) {
			$refund_id = max($refund_id, (int) $refund->get_id());
		}
		$request_id = sprintf('wcrefund-%d-%d-%s', $order->get_id(), $refund_id, substr(md5(home_url('/')), 0, 8));

		$answer = Agamsale_Api::call('refund_checkout_payment', [
			'store_order_id' => (string) $order->get_id(),
			'amount' => wc_format_decimal($amount, 2),
			'reason' => (string) $reason,
			'refund_request_id' => $request_id,
		]);
		if (is_wp_error($answer)) {
			$message = $answer->get_error_message();
			if ($answer->get_error_code() === 'AGAMSALE_UNREACHABLE') {
				$message .= ' Check Razorpay before trying again: the refund may have gone through.';
			}
			return new WP_Error('agamsale_refund', $message);
		}
		$order->add_order_note(sprintf('Refunded %s via Razorpay (%s, %s).', wc_price($amount, ['currency' => $order->get_currency()]), $answer['refund_id'] ?? '', $answer['status'] ?? ''));
		return true;
	}

	/** Agamsale's error, worded for a shopper (no internal detail). */
	private function customer_message(WP_Error $error): string {
		switch ($error->get_error_code()) {
			case 'NOT_PAID':
				return 'The payment did not go through: ' . $error->get_error_message() . '. You can try again.';
			case 'NOT_PAYABLE':
				return 'This order cannot be paid any more. Please contact the store.';
			case 'AGAMSALE_UNREACHABLE':
			case 'RAZORPAY_ERROR':
			case 'NETWORK_ERROR':
			case 'STORE_UNAVAILABLE':
				return 'Payments are temporarily unavailable. Please try again in a few minutes.';
			default:
				return 'The payment could not be completed. Please try again or contact the store.';
		}
	}
}
