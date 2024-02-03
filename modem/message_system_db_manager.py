# database_manager.py
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker
from threading import local
from message_system_db_model import Base, Station, Status, Attachment, P2PMessage
from datetime import datetime
import json
import structlog
import helpers

class DatabaseManager:
    def __init__(self, event_manger, uri='sqlite:///freedata-messages.db'):
        self.event_manager = event_manger

        self.engine = create_engine(uri, echo=False)
        self.thread_local = local()
        self.session_factory = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

        self.logger = structlog.get_logger(type(self).__name__)

    def initialize_default_values(self):
        session = self.get_thread_scoped_session()
        try:
            statuses = [
                "transmitting",
                "transmitted",
                "received",
                "failed",
                "failed_checksum",
                "aborted",
                "queued"
            ]

            # Add default statuses if they don't exist
            for status_name in statuses:
                existing_status = session.query(Status).filter_by(name=status_name).first()
                if not existing_status:
                    new_status = Status(name=status_name)
                    session.add(new_status)

            session.commit()
            self.log("Initialized database")
        except Exception as e:
            session.rollback()
            self.log(f"An error occurred while initializing default values: {e}", isWarning=True)
        finally:
            session.remove()

    def log(self, message, isWarning=False):
        msg = f"[{type(self).__name__}]: {message}"
        logger = self.logger.warn if isWarning else self.logger.info
        logger(msg)

    def get_thread_scoped_session(self):
        if not hasattr(self.thread_local, "session"):
            self.thread_local.session = scoped_session(self.session_factory)
        return self.thread_local.session

    def get_or_create_station(self, callsign, session=None):
        own_session = False
        if not session:
            session = self.get_thread_scoped_session()
            own_session = True

        try:
            station = session.query(Station).filter_by(callsign=callsign).first()
            if not station:
                self.log(f"Updating station list with {callsign}")
                station = Station(callsign=callsign, checksum=helpers.get_crc_24(callsign).hex())
                session.add(station)
                session.flush()

            if own_session:
                session.commit()  # Only commit if we created the session

            return station

        except Exception as e:

            if own_session:
                session.rollback()

        finally:
            if own_session:
                session.remove()

    def get_callsign_by_checksum(self, checksum):
        session = self.get_thread_scoped_session()
        try:
            station = session.query(Station).filter_by(checksum=checksum).first()
            if station:
                self.log(f"Found callsign [{station.callsign}] for checksum [{station.checksum}]")
                return station.callsign
            else:
                self.log(f"No callsign found for [{checksum}]")
                return None
        except Exception as e:
            self.log(f"Error fetching callsign for checksum {checksum}: {e}", isWarning=True)
            return {'status': 'failure', 'message': str(e)}
        finally:
            session.remove()

    def get_or_create_status(self, session, status_name):
        status = session.query(Status).filter_by(name=status_name).first()
        if not status:
            status = Status(name=status_name)
            session.add(status)
            session.flush()  # To get the ID immediately
        return status

    def add_message(self, message_data, direction='receive', status=None, is_read=True):
        session = self.get_thread_scoped_session()
        try:
            # Create and add the origin and destination Stations
            origin = self.get_or_create_station(message_data['origin'], session)
            destination = self.get_or_create_station(message_data['destination'], session)

            # Create and add Status if provided
            if status:
                status = self.get_or_create_status(session, status)

            # Parse the timestamp from the message ID
            timestamp = datetime.fromisoformat(message_data['id'].split('_')[2])
            # Create the P2PMessage instance
            new_message = P2PMessage(
                id=message_data['id'],
                origin_callsign=origin.callsign,
                destination_callsign=destination.callsign,
                body=message_data['body'],
                timestamp=timestamp,
                direction=direction,
                status_id=status.id if status else None,
                is_read=is_read,
                attempt=0
            )

            # Process and add attachments
            for attachment_data in message_data.get('attachments', []):
                attachment = Attachment(
                    name=attachment_data['name'],
                    data_type=attachment_data['type'],
                    data=attachment_data['data']
                )
                new_message.attachments.append(attachment)

            session.add(new_message)
            session.commit()

            self.log(f"Added data to database: {new_message.id}")
            self.event_manager.freedata_message_db_change()
            return new_message.id
        except Exception as e:
            session.rollback()
            self.log(f"error adding new message to databse with error: {e}", isWarning=True)
            self.log(f"---> please delete or update existing database", isWarning=True)
        finally:
            session.remove()

    def get_all_messages(self):
        session = self.get_thread_scoped_session()
        try:
            messages = session.query(P2PMessage).all()
            return [message.to_dict() for message in messages]

        except Exception as e:
            self.log(f"error fetching database messages with error: {e}", isWarning=True)
            self.log(f"---> please delete or update existing database", isWarning=True)

            return []

        finally:
            session.remove()

    def get_all_messages_json(self):
        messages_dict = self.get_all_messages()
        messages_with_header = {'total_messages' : len(messages_dict), 'messages' : messages_dict}
        return messages_with_header

    def get_message_by_id(self, message_id):
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                return message.to_dict()
            else:
                return None
        except Exception as e:
            self.log(f"Error fetching message with ID {message_id}: {e}", isWarning=True)
            return None
        finally:
            session.remove()

    def get_message_by_id_json(self, message_id):
        message_dict = self.get_message_by_id(message_id)
        return json.dumps(message_dict)  # Convert to JSON string

    def delete_message(self, message_id):
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                session.delete(message)
                session.commit()
                self.log(f"Deleted: {message_id}")
                self.event_manager.freedata_message_db_change()
                return {'status': 'success', 'message': f'Message {message_id} deleted'}
            else:
                return {'status': 'failure', 'message': 'Message not found'}

        except Exception as e:
            session.rollback()
            self.log(f"Error deleting message with ID {message_id}: {e}", isWarning=True)
            return {'status': 'failure', 'message': str(e)}

        finally:
            session.remove()

    def update_message(self, message_id, update_data):
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                # Update fields of the message as per update_data
                if 'body' in update_data:
                    message.body = update_data['body']
                if 'status' in update_data:
                    message.status = self.get_or_create_status(session, update_data['status'])

                session.commit()
                self.log(f"Updated: {message_id}")
                self.event_manager.freedata_message_db_change()
                return {'status': 'success', 'message': f'Message {message_id} updated'}
            else:
                return {'status': 'failure', 'message': 'Message not found'}

        except Exception as e:
            session.rollback()
            self.log(f"Error updating message with ID {message_id}: {e}", isWarning=True)
            return {'status': 'failure', 'message': str(e)}

        finally:
            session.remove()

    def get_attachments_by_message_id(self, message_id):
        session = self.get_thread_scoped_session()
        try:
            # Query for the message with the given ID
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                attachments = [attachment.to_dict() for attachment in message.attachments]
                return attachments
            else:
                return []
        except Exception as e:
            self.log(f"Error fetching attachments for message ID {message_id}: {e}", isWarning=True)
            return []
        finally:
            session.remove()

    def get_attachments_by_message_id_json(self, message_id):
        attachments = self.get_attachments_by_message_id(message_id)
        return json.dumps(attachments)

    def get_first_queued_message(self):
        session = self.get_thread_scoped_session()
        try:
            # Find the status object for "queued"
            queued_status = session.query(Status).filter_by(name='queued').first()
            if queued_status:
                # Query for the first (oldest) message with status "queued"
                message = session.query(P2PMessage)\
                    .filter_by(status=queued_status)\
                    .order_by(P2PMessage.timestamp)\
                    .first()
                if message:
                    self.log(f"Found queued message with ID {message.id}")
                    return message.to_dict()
                else:
                    return None
            else:
                self.log("Queued status not found", isWarning=True)
                return None
        except Exception as e:
            self.log(f"Error fetching the first queued message: {e}", isWarning=True)
            return None
        finally:
            session.remove()

    def increment_message_attempts(self, message_id):
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                message.attempts += 1
                session.commit()
                self.log(f"Incremented attempt count for message {message_id}")
            else:
                self.log(f"Message with ID {message_id} not found")
        except Exception as e:
            session.rollback()
            self.log(f"An error occurred while incrementing attempts for message {message_id}: {e}")
        finally:
            session.remove()

    def mark_message_as_read(self, message_id):
        session = self.get_thread_scoped_session()
        try:
            message = session.query(P2PMessage).filter_by(id=message_id).first()
            if message:
                message.is_read = True
                session.commit()
                self.log(f"Marked message {message_id} as read")
            else:
                self.log(f"Message with ID {message_id} not found")
        except Exception as e:
            session.rollback()
            self.log(f"An error occurred while marking message {message_id} as read: {e}")
        finally:
            session.remove()