# tests/test_long_lock.py
from unittest.mock import MagicMock, patch, call
import pytest


@patch("app.services.firestore_service.firestore")
def test_acquire_lock_uses_custom_key(mock_firestore):
    mock_db = MagicMock()
    mock_firestore.Client.return_value = mock_db
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.create = MagicMock()

    from app.services import firestore_service
    firestore_service._db = mock_db

    firestore_service.acquire_video_lock("owner-1", lock_key="long_video_generation")

    mock_db.collection.assert_called_with("locks")
    mock_db.collection.return_value.document.assert_called_with("long_video_generation")


@patch("app.services.firestore_service.firestore")
def test_release_lock_uses_custom_key(mock_firestore):
    mock_db = MagicMock()
    mock_firestore.Client.return_value = mock_db
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    # Make transaction work: snap not owned by caller → delete skipped
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = {"owner": "different-owner"}
    mock_doc_ref.get.return_value = mock_snap

    from app.services import firestore_service
    firestore_service._db = mock_db

    firestore_service.release_video_lock("owner-1", lock_key="long_video_generation")

    mock_db.collection.assert_called_with("locks")
    mock_db.collection.return_value.document.assert_called_with("long_video_generation")


@patch("app.services.firestore_service.firestore")
def test_acquire_lock_default_key_unchanged(mock_firestore):
    mock_db = MagicMock()
    mock_firestore.Client.return_value = mock_db
    mock_doc_ref = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.create = MagicMock()

    from app.services import firestore_service
    firestore_service._db = mock_db

    firestore_service.acquire_video_lock("owner-default")

    mock_db.collection.return_value.document.assert_called_with("video_generation")
