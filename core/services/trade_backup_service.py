import json
import csv
import io
from decimal import Decimal
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from core.models import Trade, AutobuyJob


class TradeBackupService:
    @staticmethod
    def export_trades_json(user) -> dict:
        """
        Export all trades for the given user as a structured, versioned JSON dictionary.
        """
        trades_qs = Trade.objects.filter(user=user).order_by('timestamp')
        trades_data = []

        for trade in trades_qs:
            trades_data.append({
                'order_id': trade.order_id or '',
                'exchange_name': trade.exchange_name,
                'symbol': trade.symbol,
                'job_name': trade.job_name,
                'order_type': trade.order_type,
                'amount_spent': str(trade.amount_spent),
                'amount_received': str(trade.amount_received),
                'purchase_price': str(trade.purchase_price),
                'fee_incurred': str(trade.fee_incurred),
                'status': trade.status,
                'timestamp': trade.timestamp.isoformat(),
            })

        return {
            'schema_version': '1.0',
            'version': 1,
            'app': 'moondrip',
            'exported_at': timezone.now().isoformat(),
            'exported_by': user.username,
            'source_username': user.username,
            'total_trades': len(trades_data),
            'trades': trades_data
        }

    @staticmethod
    def import_trades(user, uploaded_file) -> dict:
        """
        Import trades from a JSON backup file or CSV export into the user's account.
        Deduplicates against existing trades.
        Returns:
            {
                'success': bool,
                'imported_count': int,
                'skipped_duplicates': int,
                'total_records': int,
                'error_message': str
            }
        """
        filename = uploaded_file.name.lower()
        content = uploaded_file.read()

        try:
            if filename.endswith('.json'):
                raw_text = content.decode('utf-8')
                return TradeBackupService._import_from_json(user, raw_text)
            elif filename.endswith('.csv'):
                raw_text = content.decode('utf-8')
                return TradeBackupService._import_from_csv(user, raw_text)
            else:
                # Try parsing as JSON first, then CSV
                try:
                    raw_text = content.decode('utf-8')
                    return TradeBackupService._import_from_json(user, raw_text)
                except Exception:
                    raw_text = content.decode('utf-8')
                    return TradeBackupService._import_from_csv(user, raw_text)
        except Exception as e:
            return {
                'success': False,
                'imported_count': 0,
                'skipped_duplicates': 0,
                'total_records': 0,
                'error_message': f"Failed to read file: {str(e)}"
            }

    @staticmethod
    def _import_from_json(user, json_text: str) -> dict:
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            return {
                'success': False,
                'imported_count': 0,
                'skipped_duplicates': 0,
                'total_records': 0,
                'error_message': f"Invalid JSON format: {str(e)}"
            }

        # Check if root is dict with 'trades' or a list of trade objects
        if isinstance(data, dict):
            trades_list = data.get('trades', [])
        elif isinstance(data, list):
            trades_list = data
        else:
            return {
                'success': False,
                'imported_count': 0,
                'skipped_duplicates': 0,
                'total_records': 0,
                'error_message': "JSON file does not contain a valid trades list."
            }

        if not trades_list:
            return {
                'success': True,
                'imported_count': 0,
                'skipped_duplicates': 0,
                'total_records': 0,
                'error_message': "No trade records found in the backup file."
            }

        return TradeBackupService._save_trades(user, trades_list)

    @staticmethod
    def _import_from_csv(user, csv_text: str) -> dict:
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        if not rows:
            return {
                'success': True,
                'imported_count': 0,
                'skipped_duplicates': 0,
                'total_records': 0,
                'error_message': "CSV file is empty."
            }

        header = [col.strip().lower() for col in rows[0]]
        trades_list = []
        for r_idx, row in enumerate(rows[1:], start=2):
            if not row or all(c.strip() == '' for c in row):
                continue
            
            row_dict = {}
            for h_idx, col_name in enumerate(header):
                if h_idx < len(row):
                    row_dict[col_name] = row[h_idx].strip()

            # Map flexible CSV headers to Trade fields
            timestamp_val = (
                row_dict.get('date/time (utc)') or
                row_dict.get('date/time') or
                row_dict.get('timestamp') or
                row_dict.get('date') or
                row_dict.get('datetime') or
                ''
            )
            job_name_val = row_dict.get('job name') or row_dict.get('job_name') or 'Imported Trade'
            exchange_val = row_dict.get('exchange') or row_dict.get('exchange_name') or 'Exchange'
            symbol_val = row_dict.get('pair') or row_dict.get('symbol') or ''
            order_type_val = row_dict.get('type') or row_dict.get('order_type') or row_dict.get('side') or 'market'
            amount_val = row_dict.get('amount') or row_dict.get('amount_received') or row_dict.get('amount received') or '0'
            price_val = row_dict.get('price') or row_dict.get('purchase_price') or row_dict.get('purchase price') or '0'
            cost_val = row_dict.get('cost') or row_dict.get('amount_spent') or row_dict.get('amount spent') or '0'
            fees_val = row_dict.get('fees') or row_dict.get('fee') or row_dict.get('fee_incurred') or row_dict.get('fee incurred') or '0'
            order_id_val = row_dict.get('order id') or row_dict.get('order_id') or row_dict.get('id') or ''
            status_val = row_dict.get('status') or 'completed'

            if not symbol_val:
                continue

            trades_list.append({
                'timestamp': timestamp_val,
                'job_name': job_name_val,
                'exchange_name': exchange_val,
                'symbol': symbol_val,
                'order_type': order_type_val,
                'amount_received': amount_val,
                'purchase_price': price_val,
                'amount_spent': cost_val,
                'fee_incurred': fees_val,
                'order_id': order_id_val,
                'status': status_val
            })

        return TradeBackupService._save_trades(user, trades_list)

    @staticmethod
    def _save_trades(user, trades_list: list) -> dict:
        imported_count = 0
        skipped_duplicates = 0
        total_records = len(trades_list)

        # Cache existing jobs for user to link if name matches
        user_jobs_map = {job.name: job for job in AutobuyJob.objects.filter(user=user)}

        with transaction.atomic():
            for item in trades_list:
                symbol = str(item.get('symbol', '')).strip()
                if not symbol:
                    continue

                order_id = str(item.get('order_id', '')).strip()
                exchange_name = str(item.get('exchange_name', '')).strip() or 'Exchange'
                job_name = str(item.get('job_name', '')).strip() or 'Imported Trade'
                order_type = str(item.get('order_type', 'market')).strip() or 'market'
                status = str(item.get('status', 'completed')).strip() or 'completed'

                try:
                    amount_spent = Decimal(str(item.get('amount_spent', 0)))
                except Exception:
                    amount_spent = Decimal(0)

                try:
                    amount_received = Decimal(str(item.get('amount_received', 0)))
                except Exception:
                    amount_received = Decimal(0)

                try:
                    purchase_price = Decimal(str(item.get('purchase_price', 0)))
                except Exception:
                    purchase_price = Decimal(0)

                try:
                    fee_incurred = Decimal(str(item.get('fee_incurred', 0)))
                except Exception:
                    fee_incurred = Decimal(0)

                # Parse Timestamp
                raw_ts = item.get('timestamp')
                parsed_ts = None
                if raw_ts:
                    parsed_ts = parse_datetime(str(raw_ts))
                    if not parsed_ts:
                        # Try common ISO or standard string representations
                        try:
                            from datetime import datetime
                            parsed_ts = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
                        except Exception:
                            pass
                if not parsed_ts:
                    parsed_ts = timezone.now()
                elif timezone.is_naive(parsed_ts):
                    parsed_ts = timezone.make_aware(parsed_ts)

                # Deduplication Check
                # 1. By order_id + exchange_name if order_id is present
                if order_id:
                    exists = Trade.objects.filter(
                        user=user,
                        exchange_name=exchange_name,
                        order_id=order_id
                    ).exists()
                    if exists:
                        skipped_duplicates += 1
                        continue
                else:
                    # 2. By timestamp, symbol, amount_spent, and amount_received
                    exists = Trade.objects.filter(
                        user=user,
                        symbol=symbol,
                        amount_spent=amount_spent,
                        amount_received=amount_received,
                        timestamp__date=parsed_ts.date(),
                        timestamp__hour=parsed_ts.hour,
                        timestamp__minute=parsed_ts.minute
                    ).exists()

                    if exists:
                        skipped_duplicates += 1
                        continue

                # Optional match with existing user job
                matched_job = user_jobs_map.get(job_name)

                # Create Trade instance
                trade_obj = Trade(
                    user=user,
                    job=matched_job,
                    job_name=job_name,
                    exchange_name=exchange_name,
                    symbol=symbol,
                    order_type=order_type,
                    amount_spent=amount_spent,
                    amount_received=amount_received,
                    purchase_price=purchase_price,
                    fee_incurred=fee_incurred,
                    order_id=order_id,
                    status=status
                )
                trade_obj.save()

                # Override auto_now_add timestamp with historical timestamp
                Trade.objects.filter(pk=trade_obj.pk).update(timestamp=parsed_ts)

                imported_count += 1

        return {
            'success': True,
            'imported_count': imported_count,
            'skipped_duplicates': skipped_duplicates,
            'total_records': total_records,
            'error_message': ''
        }
