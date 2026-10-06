import { useState, useEffect } from 'react';

import { TopNav } from '@/js/components';

interface LedgerEntry {
  id: string;
  event_id: string;
  status: string;
  failure_code: string | null;
  occurred_at: string;
  created: string;
}

interface Payment {
  id: string;
  amount: number;
  currency: string;
  payment_token: string;
  status: string;
  failure_code: string | null;
  processor_reference: string | null;
  ledger: LedgerEntry[];
  created: string;
  modified: string;
}

const maskReference = (ref: string | null): string => {
  if (!ref) return '—';
  if (ref.length <= 8) return ref;
  return `${ref.slice(0, 4)}****${ref.slice(-4)}`;
};

const formatAmount = (amount: number, currency: string): string => {
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
    }).format(amount / 100);
  } catch {
    // Fall back to a plain formatted amount if the stored currency code is invalid
    return `${(amount / 100).toFixed(2)} ${currency}`;
  }
};

const Payments = () => {
  const [payments, setPayments] = useState<Payment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [paymentToken, setPaymentToken] = useState('');
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [customerEmail, setCustomerEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<string | null>(null);
  const [cardNumber, setCardNumber] = useState('');
  const [expiry, setExpiry] = useState('');
  const [cvv, setCvv] = useState('');
  const [accountNumber, setAccountNumber] = useState('');
  const [routingNumber, setRoutingNumber] = useState('');
  const [tokenMethod, setTokenMethod] = useState<'card' | 'bank'>('card');
  const [tokenizing, setTokenizing] = useState(false);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [tokenized, setTokenized] = useState<{
    token: string;
    last4: string;
    brand_or_bank_type: string;
  } | null>(null);
  const [copied, setCopied] = useState(false);

  async function handleTokenize(e: React.FormEvent) {
    e.preventDefault();
    setTokenizing(true);
    setTokenError(null);
    try {
      const response = await fetch('/api/tokenize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          tokenMethod === 'card'
            ? { method: 'card', card_number: cardNumber, expiry, cvv }
            : {
                method: 'bank',
                account_number: accountNumber,
                routing_number: routingNumber,
              },
        ),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.error || `Tokenization failed (${response.status})`);
      }
      setTokenized(body);
      setPaymentToken(body.token);
    } catch (err) {
      setTokenError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setTokenizing(false);
    }
  }

  async function handleCopyToken() {
    if (!tokenized) return;
    try {
      await navigator.clipboard.writeText(tokenized.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setTokenError('Failed to copy token');
    }
  }

  async function fetchPayments() {
    try {
      const response = await fetch('/api/payments');
      if (!response.ok) throw new Error('Failed to fetch payments');
      const data = await response.json();
      setPayments(data.results || data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchPayments();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setSubmitSuccess(null);
    try {
      const response = await fetch('/api/payments', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID(),
        },
        body: JSON.stringify({
          payment_token: paymentToken,
          amount: Math.round(Number(amount) * 100),
          currency: currency.toUpperCase() || 'USD',
          customer_email: customerEmail,
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Failed to create payment (${response.status})`);
      }
      const created = await response.json();
      setSubmitSuccess(`Payment created: ${created.id}`);
      setPaymentToken('');
      setAmount('');
      await fetchPayments();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <>
        <TopNav />
        <div className="p-8">
          <p className="text-gray-500">Loading payments…</p>
        </div>
      </>
    );
  }

  if (error) {
    return (
      <>
        <TopNav />
        <div className="p-8">
          <p className="text-red-600">Error: {error}</p>
        </div>
      </>
    );
  }

  return (
    <>
      <TopNav />
      <div className="p-8 max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold mb-6">Payments</h1>
        <form
          onSubmit={handleTokenize}
          className="mb-4 border border-gray-200 rounded-lg p-4 shadow-sm flex flex-wrap items-end gap-4"
        >
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Method
            </label>
            <select
              className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm"
              value={tokenMethod}
              onChange={(e) =>
                setTokenMethod(e.target.value as 'card' | 'bank')
              }
            >
              <option value="card">Card</option>
              <option value="bank">Bank account</option>
            </select>
          </div>
          {tokenMethod === 'card' ? (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Card number
                </label>
                <input
                  className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-48"
                  value={cardNumber}
                  onChange={(e) => setCardNumber(e.target.value)}
                  placeholder="4242 4242 4242 4242"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Expiry
                </label>
                <input
                  className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-24"
                  value={expiry}
                  onChange={(e) => setExpiry(e.target.value)}
                  placeholder="MM/YY"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  CVV
                </label>
                <input
                  className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-20"
                  value={cvv}
                  onChange={(e) => setCvv(e.target.value)}
                  placeholder="123"
                  required
                />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Account number
                </label>
                <input
                  className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-48"
                  value={accountNumber}
                  onChange={(e) => setAccountNumber(e.target.value)}
                  placeholder="000123456789"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700">
                  Routing number
                </label>
                <input
                  className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-32"
                  value={routingNumber}
                  onChange={(e) => setRoutingNumber(e.target.value)}
                  placeholder="021000021"
                  required
                />
              </div>
            </>
          )}
          <button
            type="submit"
            disabled={tokenizing}
            className="px-4 py-2 rounded-md bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 disabled:opacity-50 cursor-pointer"
          >
            {tokenizing ? 'Tokenizing…' : 'Get token'}
          </button>
          {tokenError && (
            <p className="w-full text-sm text-red-600">{tokenError}</p>
          )}
          {tokenized && (
            <div className="w-full flex items-center gap-3 text-sm text-gray-700">
              <span className="font-mono bg-gray-100 px-2 py-1 rounded break-all">
                {tokenized.token}
              </span>
              <button
                type="button"
                onClick={handleCopyToken}
                className="px-3 py-1 border border-gray-300 rounded-md text-xs hover:bg-gray-50 cursor-pointer"
              >
                {copied ? 'Copied!' : 'Copy'}
              </button>
              <span className="text-gray-500">
                {tokenized.brand_or_bank_type} ****{tokenized.last4}
              </span>
            </div>
          )}
        </form>
        <form
          onSubmit={handleCreate}
          className="mb-8 border border-gray-200 rounded-lg p-4 shadow-sm flex flex-wrap items-end gap-4"
        >
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Payment token
            </label>
            <input
              className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-64"
              value={paymentToken}
              onChange={(e) => setPaymentToken(e.target.value)}
              placeholder="tok_..."
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Amount
            </label>
            <input
              className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-32"
              type="number"
              min="0.01"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="10.00"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Currency
            </label>
            <input
              className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-20"
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
              maxLength={3}
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">
              Customer email
            </label>
            <input
              className="mt-1 border border-gray-300 rounded px-3 py-2 text-sm w-56"
              type="email"
              value={customerEmail}
              onChange={(e) => setCustomerEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="px-4 py-2 rounded-md bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 cursor-pointer"
          >
            {submitting ? 'Creating…' : 'Create payment'}
          </button>
          {submitError && (
            <p className="w-full text-sm text-red-600">{submitError}</p>
          )}
          {submitSuccess && (
            <p className="w-full text-sm text-green-700">{submitSuccess}</p>
          )}
        </form>
        {payments.length === 0 ? (
          <p className="text-gray-500">No payments found.</p>
        ) : (
          <div className="space-y-4">
            {payments.map((payment) => (
              <div
                key={payment.id}
                className="border border-gray-200 rounded-lg p-4 shadow-sm"
              >
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-lg font-semibold">
                      {formatAmount(payment.amount, payment.currency)}
                    </p>
                    <p className="text-sm text-gray-500">
                      {payment.processor_reference ?? '—'}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-3 py-1 rounded-full text-sm font-medium ${
                        payment.status === 'succeeded'
                          ? 'bg-green-100 text-green-800'
                          : payment.status === 'failed'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-yellow-100 text-yellow-800'
                      }`}
                    >
                      {payment.status}
                    </span>
                    <button
                      type="button"
                      className="text-sm text-blue-600 hover:underline cursor-pointer"
                      onClick={() =>
                        setExpandedId(
                          expandedId === payment.id ? null : payment.id,
                        )
                      }
                    >
                      {expandedId === payment.id ? 'Hide' : 'Show'} ledger
                    </button>
                  </div>
                </div>
                {expandedId === payment.id && (
                  <div className="mt-4 border-t pt-4">
                    <h3 className="text-sm font-semibold mb-2">
                      Ledger History
                    </h3>
                    {payment.ledger.length === 0 ? (
                      <p className="text-sm text-gray-400">
                        No ledger entries yet.
                      </p>
                    ) : (
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-gray-500">
                            <th className="pb-2">Event</th>
                            <th className="pb-2">Status</th>
                            <th className="pb-2">Failure Code</th>
                            <th className="pb-2">Occurred At</th>
                          </tr>
                        </thead>
                        <tbody>
                          {payment.ledger.map((entry) => (
                            <tr key={entry.id} className="border-t">
                              <td className="py-2 font-mono text-xs">
                                {maskReference(entry.event_id)}
                              </td>
                              <td className="py-2">
                                <span
                                  className={`px-2 py-0.5 rounded text-xs ${
                                    entry.status === 'succeeded'
                                      ? 'bg-green-50 text-green-700'
                                      : entry.status === 'failed'
                                        ? 'bg-red-50 text-red-700'
                                        : 'bg-yellow-50 text-yellow-700'
                                  }`}
                                >
                                  {entry.status}
                                </span>
                              </td>
                              <td className="py-2 text-gray-500">
                                {entry.failure_code || '—'}
                              </td>
                              <td className="py-2 text-gray-500">
                                {new Date(entry.occurred_at).toLocaleString()}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
};

export default Payments;
