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
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
  }).format(amount / 100);
};

const Payments = () => {
  const [payments, setPayments] = useState<Payment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
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
    fetchPayments();
  }, []);

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
                      {maskReference(payment.processor_reference)}
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
