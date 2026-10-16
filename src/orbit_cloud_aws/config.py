# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Validated AWS Resource Explorer configuration."""

from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

_REGION = re.compile(r"[a-z0-9-]{3,64}\Z")
_VIEW_ARN = re.compile(
    r"arn:[a-z0-9-]+:resource-explorer-2:[a-z0-9-]+:[0-9]{12}:view/[A-Za-z0-9/_+=,.@-]+\Z"
)


class AWSCloudInventoryConfig(BaseModel):
    """Bounded settings for one AWS Resource Explorer view.

    The adapter uses the standard aiobotocore credential chain and never accepts credentials in
    this model. ``region_name`` selects the API endpoint; the view ARN must identify an already
    configured view.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    region_name: StrictStr = Field(min_length=3, max_length=64)
    view_arn: StrictStr = Field(min_length=32, max_length=512, repr=False)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    request_timeout_seconds: float = Field(default=90.0, gt=0, le=900)
    max_concurrency: int = Field(default=8, ge=1, le=32)
    retry_total_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum AWS SDK attempts per request, including the initial attempt.",
    )

    @field_validator("region_name")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """Require a valid bounded AWS region identifier."""
        if _REGION.fullmatch(value) is None:
            raise ValueError("AWS region must be a bounded region identifier.")
        return value

    @field_validator("view_arn")
    @classmethod
    def validate_view_arn(cls, value: str) -> str:
        """Require a valid Resource Explorer view ARN."""
        if _VIEW_ARN.fullmatch(value) is None:
            raise ValueError("view_arn must be a valid AWS Resource Explorer view ARN.")
        return value

    @model_validator(mode="after")
    def validate_timeouts(self) -> AWSCloudInventoryConfig:
        """Reject non-finite timeout values after field range validation."""
        for name, value in (
            ("connect_timeout_seconds", self.connect_timeout_seconds),
            ("read_timeout_seconds", self.read_timeout_seconds),
            ("request_timeout_seconds", self.request_timeout_seconds),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite.")
        return self


__all__ = ["AWSCloudInventoryConfig"]
