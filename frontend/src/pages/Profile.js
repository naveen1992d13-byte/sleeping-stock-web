import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { API, useAuth } from '@/App';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Mail, Phone, Calendar, Activity, Lock, MapPin, Building, Users, Shield } from 'lucide-react';
import { toast } from 'sonner';

export function Profile() {
  const { userId } = useParams();
  const { user: currentUser } = useAuth();
  const navigate = useNavigate();
  const [profile, setProfile] = useState(null);
  const [activityLogs, setActivityLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('edit');
  
  const [editForm, setEditForm] = useState({
    username: '',
    email: '',
    phone: ''
  });
  
  const [passwordForm, setPasswordForm] = useState({
    old_password: '',
    new_password: '',
    confirm_password: ''
  });

  const targetUserId = userId || currentUser?.id;

  useEffect(() => {
    if (targetUserId) {
      fetchProfileData();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetUserId]);

  const fetchProfileData = async () => {
    try {
      const [profileRes, logsRes] = await Promise.all([
        axios.get(`${API}/profile/${targetUserId}`),
        axios.get(`${API}/profile/${targetUserId}/activity-logs`)
      ]);

      setProfile(profileRes.data);
      setEditForm({
        username: profileRes.data.username,
        email: profileRes.data.email,
        phone: profileRes.data.phone || ''
      });
      setActivityLogs(logsRes.data);
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error fetching profile');
      if (error.response?.status === 403) {
        navigate('/');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleUpdateProfile = async (e) => {
    e.preventDefault();
    try {
      await axios.put(`${API}/profile/${targetUserId}`, editForm);
      toast.success('Profile updated successfully');
      fetchProfileData();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error updating profile');
    }
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    
    if (passwordForm.new_password !== passwordForm.confirm_password) {
      toast.error('New passwords do not match');
      return;
    }

    try {
      await axios.put(`${API}/profile/${targetUserId}/password`, {
        old_password: passwordForm.old_password,
        new_password: passwordForm.new_password
      });
      toast.success('Password changed successfully');
      setPasswordForm({ old_password: '', new_password: '', confirm_password: '' });
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Error changing password');
    }
  };

  const formatDate = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-IN', { day: '2-digit', month: '2-digit', year: 'numeric' });
  };

  const formatDateTime = (dateString) => {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleString('en-IN', { 
      day: '2-digit', 
      month: '2-digit', 
      year: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  };

  if (loading) return null;

  if (!profile) {
    return (
      <div className="flex items-center justify-center h-64" style={{ color: '#047857' }}>
        Profile not found
      </div>
    );
  }

  const isOwnProfile = currentUser?.id === targetUserId;

  return (
    <div className="space-y-4" data-testid="profile-page">
      {/* User Information Header Card */}
      <div 
        className="rounded-2xl p-6"
        style={{ backgroundColor: '#34D399' }}
        data-testid="profile-header"
      >
        <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6">
          {/* Left side - User name and basic info */}
          <div className="flex-1">
            <h1 
              className="text-3xl font-bold mb-4"
              style={{ color: '#FFFFFF' }}
            >
              {profile.username}
            </h1>
            
            <div className="space-y-3">
              {/* Mail ID */}
              <div className="flex items-center gap-3">
                <Mail className="h-5 w-5" style={{ color: '#D1FAE5' }} />
                <span style={{ color: '#D1FAE5' }}>Mail ID:</span>
                <span className="font-medium" style={{ color: '#FFFFFF' }}>
                  {profile.email}
                </span>
              </div>
              
              {/* Mobile */}
              {profile.phone && (
                <div className="flex items-center gap-3">
                  <Phone className="h-5 w-5" style={{ color: '#D1FAE5' }} />
                  <span style={{ color: '#D1FAE5' }}>Mobile:</span>
                  <span className="font-medium" style={{ color: '#FFFFFF' }}>
                    {profile.phone}
                  </span>
                </div>
              )}
              
              {/* Last Login */}
              <div className="flex items-center gap-3">
                <Activity className="h-5 w-5" style={{ color: '#D1FAE5' }} />
                <span style={{ color: '#D1FAE5' }}>Last Login:</span>
                <span className="font-medium" style={{ color: '#FFFFFF' }}>
                  {formatDateTime(profile.last_login)}
                </span>
              </div>
              
              {/* Joined */}
              <div className="flex items-center gap-3">
                <Calendar className="h-5 w-5" style={{ color: '#D1FAE5' }} />
                <span style={{ color: '#D1FAE5' }}>Joined:</span>
                <span className="font-medium" style={{ color: '#FFFFFF' }}>
                  {formatDate(profile.created_at)}
                </span>
              </div>
            </div>
          </div>
          
          {/* Right side - Role, Brand, Group, Location */}
          <div className="lg:text-right space-y-3">
            <div className="flex items-center gap-3 lg:justify-end">
              <Shield className="h-5 w-5" style={{ color: '#D1FAE5' }} />
              <span style={{ color: '#D1FAE5' }}>Role:</span>
              <span className="font-medium capitalize" style={{ color: '#FFFFFF' }}>
                {profile.role}
              </span>
            </div>
            
            <div className="flex items-center gap-3 lg:justify-end">
              <Building className="h-5 w-5" style={{ color: '#D1FAE5' }} />
              <span style={{ color: '#D1FAE5' }}>Brand:</span>
              <span className="font-medium" style={{ color: '#FFFFFF' }}>
                {profile.brand || 'N/A'}
              </span>
            </div>
            
            <div className="flex items-center gap-3 lg:justify-end">
              <Users className="h-5 w-5" style={{ color: '#D1FAE5' }} />
              <span style={{ color: '#D1FAE5' }}>Group:</span>
              <span className="font-medium" style={{ color: '#FFFFFF' }}>
                {profile.group || 'N/A'}
              </span>
            </div>
            
            <div className="flex items-center gap-3 lg:justify-end">
              <MapPin className="h-5 w-5" style={{ color: '#D1FAE5' }} />
              <span style={{ color: '#D1FAE5' }}>Location:</span>
              <span className="font-medium" style={{ color: '#FFFFFF' }}>
                {profile.location || 'N/A'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Tab Navigation */}
      <div 
        className="flex gap-2 p-1 rounded-xl"
        style={{ backgroundColor: '#A7F3D0' }}
      >
        <button
          onClick={() => setActiveTab('edit')}
          className="flex-1 py-3 px-4 rounded-lg font-medium transition-all"
          style={{
            backgroundColor: activeTab === 'edit' ? '#059669' : 'transparent',
            color: activeTab === 'edit' ? '#FFFFFF' : '#047857'
          }}
          data-testid="edit-tab"
        >
          Edit Profile
        </button>
        {isOwnProfile && (
          <button
            onClick={() => setActiveTab('password')}
            className="flex-1 py-3 px-4 rounded-lg font-medium transition-all"
            style={{
              backgroundColor: activeTab === 'password' ? '#059669' : 'transparent',
              color: activeTab === 'password' ? '#FFFFFF' : '#047857'
            }}
            data-testid="password-tab"
          >
            Change Password
          </button>
        )}
        <button
          onClick={() => setActiveTab('activity')}
          className="flex-1 py-3 px-4 rounded-lg font-medium transition-all"
          style={{
            backgroundColor: activeTab === 'activity' ? '#059669' : 'transparent',
            color: activeTab === 'activity' ? '#FFFFFF' : '#047857'
          }}
          data-testid="activity-tab"
        >
          Activity Log
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === 'edit' && (
        <div 
          className="rounded-2xl p-6"
          style={{ backgroundColor: '#34D399' }}
          data-testid="edit-profile-section"
        >
          <h2 
            className="text-2xl font-bold mb-6"
            style={{ color: '#FFFFFF' }}
          >
            Edit Profile Information
          </h2>
          
          <form onSubmit={handleUpdateProfile} className="space-y-5">
            <div>
              <Label 
                htmlFor="username" 
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                User Name
              </Label>
              <Input
                id="username"
                data-testid="edit-username-input"
                value={editForm.username}
                onChange={(e) => setEditForm({ ...editForm, username: e.target.value })}
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
                required
              />
            </div>
            
            <div>
              <Label 
                htmlFor="email"
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                Email
              </Label>
              <Input
                id="email"
                type="email"
                data-testid="edit-email-input"
                value={editForm.email}
                onChange={(e) => setEditForm({ ...editForm, email: e.target.value })}
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
                required
              />
            </div>
            
            <div>
              <Label 
                htmlFor="phone"
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                Phone Number
              </Label>
              <Input
                id="phone"
                data-testid="edit-phone-input"
                value={editForm.phone}
                onChange={(e) => setEditForm({ ...editForm, phone: e.target.value })}
                placeholder="+91 00000 00000"
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
              />
            </div>
            
            <Button 
              type="submit" 
              data-testid="save-profile-button"
              className="mt-4 px-8 py-3 rounded-lg font-medium"
              style={{ 
                backgroundColor: '#FFFFFF', 
                color: '#047857',
                border: 'none'
              }}
            >
              Save Changes
            </Button>
          </form>
        </div>
      )}

      {activeTab === 'password' && isOwnProfile && (
        <div 
          className="rounded-2xl p-6"
          style={{ backgroundColor: '#34D399' }}
          data-testid="change-password-section"
        >
          <h2 
            className="text-2xl font-bold mb-6"
            style={{ color: '#FFFFFF' }}
          >
            Change Password
          </h2>
          
          <form onSubmit={handleChangePassword} className="space-y-5">
            <div>
              <Label 
                htmlFor="old_password"
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                Current Password
              </Label>
              <Input
                id="old_password"
                type="password"
                data-testid="old-password-input"
                value={passwordForm.old_password}
                onChange={(e) => setPasswordForm({ ...passwordForm, old_password: e.target.value })}
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
                required
              />
            </div>
            
            <div>
              <Label 
                htmlFor="new_password"
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                New Password
              </Label>
              <Input
                id="new_password"
                type="password"
                data-testid="new-password-input"
                value={passwordForm.new_password}
                onChange={(e) => setPasswordForm({ ...passwordForm, new_password: e.target.value })}
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
                required
              />
            </div>
            
            <div>
              <Label 
                htmlFor="confirm_password"
                className="text-sm font-medium mb-2 block"
                style={{ color: '#D1FAE5' }}
              >
                Confirm New Password
              </Label>
              <Input
                id="confirm_password"
                type="password"
                data-testid="confirm-password-input"
                value={passwordForm.confirm_password}
                onChange={(e) => setPasswordForm({ ...passwordForm, confirm_password: e.target.value })}
                className="w-full rounded-lg py-3"
                style={{ 
                  backgroundColor: '#FFFFFF', 
                  border: '1px solid #D1D5DB',
                  color: '#374151'
                }}
                required
              />
            </div>
            
            <Button 
              type="submit" 
              data-testid="change-password-button"
              className="mt-4 px-8 py-3 rounded-lg font-medium flex items-center gap-2"
              style={{ 
                backgroundColor: '#FFFFFF', 
                color: '#047857',
                border: 'none'
              }}
            >
              <Lock className="h-4 w-4" />
              Change Password
            </Button>
          </form>
        </div>
      )}

      {activeTab === 'activity' && (
        <div 
          className="rounded-2xl p-6"
          style={{ backgroundColor: '#34D399' }}
          data-testid="activity-log-section"
        >
          <h2 
            className="text-2xl font-bold mb-6"
            style={{ color: '#FFFFFF' }}
          >
            Activity Log
          </h2>
          
          {activityLogs.length === 0 ? (
            <p style={{ color: '#D1FAE5' }} className="text-center py-8">
              No activity logs available
            </p>
          ) : (
            <div className="space-y-3">
              {activityLogs.map((log) => (
                <div 
                  key={log.id} 
                  className="flex items-center justify-between p-4 rounded-lg"
                  style={{ backgroundColor: 'rgba(255,255,255,0.2)' }}
                  data-testid={`log-${log.id}`}
                >
                  <div className="flex items-center gap-3">
                    <Activity className="h-5 w-5" style={{ color: '#D1FAE5' }} />
                    <span style={{ color: '#FFFFFF' }}>{log.action}</span>
                  </div>
                  <span style={{ color: '#D1FAE5' }} className="text-sm">
                    {formatDateTime(log.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
