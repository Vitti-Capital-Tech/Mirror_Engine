import { redirect } from 'next/navigation';

/** There is no signup any more: an unrecognised email signs in and creates the
 *  account in the same step, so this page had nothing to ask for. Kept as a
 *  redirect rather than deleted so an old bookmark or link lands on the login
 *  form instead of a 404. */
export default function SignupPage() {
  redirect('/login');
}
