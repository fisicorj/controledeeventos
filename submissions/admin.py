from django.contrib import admin

from .models import CallForPapers, Review, Submission


@admin.register(CallForPapers)
class CallForPapersAdmin(admin.ModelAdmin):
    list_display = ('activity', 'opens_at', 'closes_at', 'blind_review')
    filter_horizontal = ('reviewers',)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ('title', 'call', 'author', 'status', 'version', 'created_at')
    list_filter = ('status',)
    search_fields = ('title', 'author__username', 'author__email')
    raw_id_fields = ('author', 'decided_by', 'session')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('submission', 'reviewer', 'recommendation', 'score', 'submitted_at')
    raw_id_fields = ('submission', 'reviewer')
