import { redirect } from 'next/navigation';

/** There is no landing page any more.
 *
 *  It existed to sell the product and hand visitors a "Get started" button —
 *  but the only thing behind that button is now a single email box, so the
 *  marketing page was a wall between someone and a one-field form.
 *
 *  Redirects rather than rendering the form here, so the login lives at exactly
 *  one URL. A signed-in visitor is not stranded on it: AppShell bounces anyone
 *  authenticated off a public route to /positions.
 */
export default function Home() {
  redirect('/login');
}
