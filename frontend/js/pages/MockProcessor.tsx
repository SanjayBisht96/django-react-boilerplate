import { useState, useEffect } from 'react';

import { TopNav } from '@/js/components';

interface Charge {
  id: string;
  processor_reference: string;
  amount: number;
  currency: string;
  status: string;
  failure_code: string | null;
  method: string;
  last4: string;
  brand_or_bank_type: string;
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

const MockProcessor = () => {
  const [charges, setCharges] = useState<Charge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchCharges() {
      try {
        const response = await fetch('/processor/charges');
        if (!response.ok) throw new Error('Failed to fetch charges');
        const data = await response.json();
        setCharges(data.results || data);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    }
    fetchCharges();
  }, []);

  return (
    <>
      <TopNav />
      <div className="p-8 max-w-6xl mx-auto">
        <h1 className="text-3xl font-bold mb-6">Mock Processor Requests</h1>
        {loading ? (
          <p className="text-gray-500">Loading…</p>
        ) : error ? (
          <p className="text-red-600">Error: {error}</p>
        ) : charges.length === 0 ? (
          <p className="text-gray-500">No requests yet.</p>
        ) : (
          <table className="w-full text-sm border border-gray-200 rounded-lg">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="p-3">Reference</th>
                <th className="p-3">Amount</th>
                <th className="p-3">Method</th>
                <th className="p-3">Status</th>
                <th className="p-3">Failure Code</th>
                <th className="p-3">Created</th>
              </tr>
            </thead>
            <tbody>
              {charges.map((charge) => (
                <tr key={charge.id} className="border-t border-gray-100">
                  <td className="p-3 font-mono text-xs">
                    {maskReference(charge.processor_reference)}
                  </td>
                  <td className="p-3">
                    {formatAmount(charge.amount, charge.currency)}
                  </td>
                  <td className="p-3">
                    {charge.brand_or_bank_type} • {charge.method} ****{charge.last4}
                  </td>
                  <td className="p-3">
                    <span
                      className={`px-2 py-0.5 rounded text-xs ${
                        charge.status === 'succeeded'
                          ? 'bg-green-50 text-green-700'
                          : charge.status === 'failed'
                            ? 'bg-red-50 text-red-700'
                            : 'bg-yellow-50 text-yellow-700'
                      }`}
                    >
                      {charge.status}
                    </span>
                  </td>
                  <td className="p-3 text-gray-500">
                    {charge.failure_code || '—'}
                  </td>
                  <td className="p-3 text-gray-500">
                    {new Date(charge.created).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
};

export default MockProcessor;
