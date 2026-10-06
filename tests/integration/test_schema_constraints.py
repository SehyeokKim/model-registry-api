"""DB 제약이 잘못된 데이터를 실제로 막는지 확인한다.

API 검증과 별개로, 다른 경로로 들어온 데이터도 DB가 최종적으로 막아야 한다.
"""

import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.entities import (
    MLModel,
    MLModelVersion,
    ModelVersionStatus,
    Organization,
    TaskType,
    User,
    UserRole,
)


def _violated_constraint(error: pytest.ExceptionInfo[IntegrityError]) -> str | None:
    return error.value.orig.diag.constraint_name  # type: ignore[union-attr]


def _organization(session: Session, name: str = "Acme") -> Organization:
    organization = Organization(name=name)
    session.add(organization)
    session.flush()
    return organization


def _customer(session: Session, organization: Organization) -> User:
    user = User(
        organization_id=organization.id,
        email=f"{uuid.uuid4()}@example.com",
        name="Customer",
        role=UserRole.CUSTOMER,
    )
    session.add(user)
    session.flush()
    return user


def _model(
    session: Session, user: User, name: str = "churn-predictor", **fields: object
) -> MLModel:
    assert user.organization_id is not None
    model = MLModel(
        organization_id=user.organization_id,
        created_by=user.id,
        name=name,
        task_type=TaskType.CLASSIFICATION,
        **fields,
    )
    session.add(model)
    session.flush()
    return model


def _version(session: Session, model: MLModel, version_no: int, **fields: object) -> MLModelVersion:
    version = MLModelVersion(
        model_id=model.id, version_no=version_no, created_by=model.created_by, **fields
    )
    session.add(version)
    session.flush()
    return version


@pytest.fixture
def customer(db_session: Session) -> User:
    return _customer(db_session, _organization(db_session))


class TestDefaults:
    def test_server_defaults_are_applied(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer)
        version = _version(db_session, model, 1)
        db_session.commit()
        db_session.refresh(model)
        db_session.refresh(version)

        assert isinstance(model.id, uuid.UUID)
        assert model.row_version == 1
        assert model.created_at.tzinfo is not None
        assert version.status is ModelVersionStatus.PENDING


class TestUsers:
    def test_customer_requires_organization(self, db_session: Session) -> None:
        db_session.add(User(email="c@example.com", name="C", role=UserRole.CUSTOMER))

        with pytest.raises(IntegrityError) as error:
            db_session.flush()

        assert _violated_constraint(error) == "ck_users_customer_has_organization"

    def test_internal_user_cannot_belong_to_organization(self, db_session: Session) -> None:
        organization = _organization(db_session)
        db_session.add(
            User(
                organization_id=organization.id,
                email="e@example.com",
                name="E",
                role=UserRole.ML_ENGINEER,
            )
        )

        with pytest.raises(IntegrityError) as error:
            db_session.flush()

        assert _violated_constraint(error) == "ck_users_customer_has_organization"

    def test_unknown_role_is_rejected(self, db_session: Session) -> None:
        with pytest.raises(IntegrityError) as error:
            db_session.execute(
                text("INSERT INTO users (email, name, role) VALUES ('x@example.com', 'X', 'ROOT')")
            )

        assert _violated_constraint(error) == "ck_users_role"


class TestModelNames:
    def test_active_name_is_unique_within_organization(
        self, db_session: Session, customer: User
    ) -> None:
        _model(db_session, customer, name="same")

        with pytest.raises(IntegrityError) as error:
            _model(db_session, customer, name="same")

        assert _violated_constraint(error) == "uq_models_organization_id_name_active"

    def test_name_can_be_reused_after_soft_delete(
        self, db_session: Session, customer: User
    ) -> None:
        deleted_at = datetime.now(UTC)
        _model(
            db_session,
            customer,
            name="same",
            deleted_at=deleted_at,
            purge_at=deleted_at + timedelta(days=30),
        )

        _model(db_session, customer, name="same")

    def test_same_name_is_allowed_in_other_organization(
        self, db_session: Session, customer: User
    ) -> None:
        other = _customer(db_session, _organization(db_session, "Other"))
        _model(db_session, customer, name="same")

        _model(db_session, other, name="same")

    @pytest.mark.parametrize(
        "name",
        [
            unicodedata.normalize("NFD", "한국모델"),
            " leading-space",
            "trailing-space ",
            "",
        ],
    )
    def test_unnormalized_name_is_rejected(
        self, db_session: Session, customer: User, name: str
    ) -> None:
        with pytest.raises(IntegrityError) as error:
            _model(db_session, customer, name=name)

        assert _violated_constraint(error) == "ck_models_name_normalized"

    def test_nfc_korean_and_japanese_names_are_allowed(
        self, db_session: Session, customer: User
    ) -> None:
        _model(db_session, customer, name=unicodedata.normalize("NFC", "한국모델"))
        _model(db_session, customer, name=unicodedata.normalize("NFC", "需要予測モデル"))


class TestSoftDelete:
    def test_deleted_at_and_purge_at_must_be_set_together(
        self, db_session: Session, customer: User
    ) -> None:
        with pytest.raises(IntegrityError) as error:
            _model(db_session, customer, deleted_at=datetime.now(UTC))

        assert _violated_constraint(error) == "ck_models_soft_delete_pair"

    def test_purge_at_must_be_after_deleted_at(self, db_session: Session, customer: User) -> None:
        now = datetime.now(UTC)

        with pytest.raises(IntegrityError) as error:
            _model(db_session, customer, deleted_at=now, purge_at=now - timedelta(seconds=1))

        assert _violated_constraint(error) == "ck_models_purge_after_delete"


class TestVersions:
    def test_version_no_is_unique_per_model(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer)
        _version(db_session, model, 1)

        with pytest.raises(IntegrityError) as error:
            _version(db_session, model, 1)

        assert _violated_constraint(error) == "uq_model_versions_model_id_version_no"

    def test_version_no_must_be_positive(self, db_session: Session, customer: User) -> None:
        with pytest.raises(IntegrityError) as error:
            _version(db_session, _model(db_session, customer), 0)

        assert _violated_constraint(error) == "ck_model_versions_version_no_positive"

    def test_parent_must_belong_to_same_model(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer, name="a")
        other_model = _model(db_session, customer, name="b")
        other_parent = _version(db_session, other_model, 1)

        with pytest.raises(IntegrityError) as error:
            _version(db_session, model, 1, parent_version_id=other_parent.id)

        assert _violated_constraint(error) == "fk_model_versions_parent_version"

    def test_parent_in_same_model_is_allowed(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer)
        parent = _version(db_session, model, 1)

        child = _version(db_session, model, 2, parent_version_id=parent.id)

        assert child.parent_version_id == parent.id

    def test_version_cannot_be_its_own_parent(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer)
        version_id = uuid.uuid4()

        with pytest.raises(IntegrityError) as error:
            _version(db_session, model, 1, id=version_id, parent_version_id=version_id)

        assert _violated_constraint(error) == "ck_model_versions_parent_not_self"

    @pytest.mark.parametrize("status", [ModelVersionStatus.READY, ModelVersionStatus.ARCHIVED])
    def test_ready_or_archived_requires_artifact(
        self, db_session: Session, customer: User, status: ModelVersionStatus
    ) -> None:
        with pytest.raises(IntegrityError) as error:
            _version(db_session, _model(db_session, customer), 1, status=status)

        assert _violated_constraint(error) == "ck_model_versions_ready_has_artifact"

    def test_deleting_model_cascades_to_versions(self, db_session: Session, customer: User) -> None:
        model = _model(db_session, customer)
        parent = _version(db_session, model, 1)
        _version(db_session, model, 2, parent_version_id=parent.id)
        db_session.commit()

        db_session.execute(text("DELETE FROM models WHERE id = :id"), {"id": model.id})
        db_session.commit()

        assert db_session.scalars(select(MLModelVersion)).all() == []
