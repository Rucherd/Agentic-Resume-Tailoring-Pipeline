from __future__ import annotations

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class PersonalInfo(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""


class EducationEntry(BaseModel):
    id: str
    institution: str
    degree: str
    location: str = ""
    start: str = ""
    end: str = ""
    details: List[str] = Field(default_factory=list)


class Bullet(BaseModel):
    id: str
    text: str
    skills: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    variants: Dict[str, str] = Field(default_factory=dict)


class ExperienceProfile(BaseModel):
    title: str = Field(min_length=1)
    bullets: List[Bullet] = Field(min_length=1)


class ExperienceEntry(BaseModel):
    id: str
    company: str
    title: str
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: List[Bullet] = Field(default_factory=list)
    profiles: Dict[str, ExperienceProfile] = Field(default_factory=dict)


class ProjectEntry(BaseModel):
    id: str
    name: str
    subtitle: str = ""
    date: str = ""
    bullets: List[Bullet] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)


class SkillCategory(BaseModel):
    category: str
    items: List[str] = Field(default_factory=list)


class ResumeData(BaseModel):
    personal: PersonalInfo
    education: List[EducationEntry] = Field(default_factory=list)
    experience: List[ExperienceEntry] = Field(default_factory=list)
    projects: List[ProjectEntry] = Field(default_factory=list)
    skills: List[SkillCategory] = Field(default_factory=list)

    def all_bullets(self) -> dict[str, Bullet]:
        result: dict[str, Bullet] = {}
        for item in self.experience:
            for bullet in item.bullets:
                result[bullet.id] = bullet
        for item in self.projects:
            for bullet in item.bullets:
                result[bullet.id] = bullet
        return result

    def all_item_ids(self) -> set[str]:
        return {x.id for x in self.experience} | {x.id for x in self.projects}

    def all_skill_names(self) -> set[str]:
        return {skill for category in self.skills for skill in category.items}
